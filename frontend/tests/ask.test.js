import { NextRequest } from 'next/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { answerNotices, askErrorMessage, citationHref, foundViaLabel, parseLineRange } from '@/lib/ask';
import { handleAsk } from '@/lib/server/ask-route';
import { CSRF_HEADER_NAME, SESSION_COOKIE_NAME, createSessionToken, csrfTokenFor } from '@/lib/server/session';

vi.mock('@/lib/server/dal', () => ({ requireSession: vi.fn(async () => ({ sid: 'test' })) }));

const response = (overrides = {}) => ({
  repository_id: 3,
  question: 'q',
  answer: { text: '[stub-llm] placeholder', generator: 'stub', is_placeholder: true },
  retrieval: { embedding_provider: 'deterministic_local', semantic_embeddings: false, graph: 'not_configured', graph_built: null },
  sources: [],
  context_chunk_count: 0,
  warnings: [],
  ...overrides,
});

describe('citations', () => {
  it('link to the exact file and line range, encoded, with an anchor', () => {
    const href = citationHref(3, { relative_path: 'src/a b/x&y.py', start_line: 12, end_line: 30 });
    expect(href).toBe('/repositories/3?path=src%2Fa+b%2Fx%26y.py&lines=12-30#L12');
  });

  it.each([
    ['12-30', { start: 12, end: 30 }],
    ['7', { start: 7, end: 7 }],
  ])('parses %s', (value, expected) => {
    expect(parseLineRange(value)).toEqual(expected);
  });

  it.each([undefined, '', '0', '30-12', 'a-b', '1-2-3', '-5', '1-99999999', '<script>'])('rejects line range %p', (value) => {
    expect(parseLineRange(value)).toBeNull();
  });

  it('labels retrieval methods without inventing any', () => {
    expect(['vector', 'keyword', 'graph:parent_class', 'other'].map(foundViaLabel)).toEqual(['vector', 'keyword', 'graph · parent class', 'other']);
  });
});

describe('answer notices are derived from reported fields only', () => {
  it('flags stub output, non-semantic embeddings, and missing graph', () => {
    const titles = answerNotices(response()).map((n) => n.title);
    expect(titles).toEqual(['Placeholder answer', 'Non-semantic embeddings', 'No graph evidence']);
  });

  it('shows nothing extra for a real LLM, semantic embeddings, and a used graph', () => {
    const real = response({
      answer: { text: 'x', generator: 'anthropic', is_placeholder: false },
      retrieval: { embedding_provider: 'openai', semantic_embeddings: true, graph: 'used', graph_built: true },
    });
    expect(answerNotices(real)).toEqual([]);
  });

  it('reports an unbuilt graph and no context', () => {
    const titles = answerNotices(
      response({
        answer: { text: 'x', generator: 'anthropic', is_placeholder: false },
        retrieval: { embedding_provider: 'openai', semantic_embeddings: true, graph: 'used', graph_built: false },
        warnings: ['no_relevant_context'],
      }),
    ).map((n) => n.title);
    expect(titles).toEqual(['No graph for this repository', 'No relevant code found']);
  });

  it('error messages fall back safely', () => {
    expect(askErrorMessage('answer_generation_failed')).toMatch(/could not be generated/);
    // An embedding-model mismatch is now refused (409), not answered with a warning.
    expect(askErrorMessage('reindex_required')).toMatch(/Re-index it/);
    expect(askErrorMessage('<img src=x>')).toBe('The question could not be answered.');
  });
});

describe('POST /api/repositories/:id/ask', () => {
  const ORIGIN = 'http://127.0.0.1:3000';
  const SECRET = 'ask-route-secret-'.padEnd(48, 'a');
  let fetchMock;

  beforeEach(() => {
    vi.stubEnv('SESSION_SECRET', SECRET);
    vi.stubEnv('OPERATOR_PASSWORD_HASH', 'configured');
    vi.stubEnv('BACKEND_URL', 'http://127.0.0.1:8000');
    vi.stubEnv('API_SERVICE_TOKEN', 'service-token-'.padEnd(48, 'z'));
    vi.stubEnv('APP_ORIGIN', '');
    fetchMock = vi.fn(async () => Response.json(response()));
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  function request(body, { csrf = true, session = true, origin = ORIGIN } = {}) {
    const { token, session: s } = createSessionToken(SECRET, 900);
    const headers = { host: '127.0.0.1:3000', 'content-type': 'application/json' };
    if (origin) headers.origin = origin;
    if (session) headers.cookie = `${SESSION_COOKIE_NAME}=${token}`;
    if (csrf) headers[CSRF_HEADER_NAME] = csrfTokenFor(s, SECRET);
    return new NextRequest(`${ORIGIN}/api/repositories/3/ask`, { method: 'POST', headers, body: JSON.stringify(body) });
  }

  it('forwards only the trimmed question to the backend ask endpoint', async () => {
    const res = await handleAsk(request({ question: '  where is escape?  ', repository_path: '/etc', root_path: '/' }), '3');
    expect(res.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('http://127.0.0.1:8000/api/v1/repositories/3/ask');
    expect(JSON.parse(init.body)).toEqual({ question: 'where is escape?' });
  });

  it.each([
    ['no session', { session: false }, 401],
    ['no CSRF token', { csrf: false }, 403],
    ['cross-site origin', { origin: 'https://evil.example' }, 403],
  ])('rejects %s (Ask can spend LLM credits)', async (_label, options, status) => {
    expect((await handleAsk(request({ question: 'q' }, options), '3')).status).toBe(status);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([{}, { question: '' }, { question: '   ' }, { question: 7 }, { question: 'q'.repeat(2001) }])('rejects body %j', async (body) => {
    const res = await handleAsk(request(body), '3');
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: 'invalid_question' });
  });

  it.each(['0', 'abc', '../1'])('rejects repository id %p', async (id) => {
    expect((await handleAsk(request({ question: 'q' }), id)).status).toBe(400);
  });

  it('passes backend error codes through without detail', async () => {
    fetchMock.mockResolvedValueOnce(Response.json({ error: 'answer_generation_failed' }, { status: 502 }));
    const res = await handleAsk(request({ question: 'q' }), '3');
    expect((await res.json())).toEqual({ error: 'answer_generation_failed' });
  });
});
