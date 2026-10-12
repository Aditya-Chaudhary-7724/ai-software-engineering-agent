import { NextRequest } from 'next/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { handleGetJob, handleReindex, handleSubmitIngestion } from '@/lib/server/job-routes';
import { CSRF_HEADER_NAME, SESSION_COOKIE_NAME, createSessionToken, csrfTokenFor } from '@/lib/server/session';

vi.mock('@/lib/server/dal', () => ({ requireSession: vi.fn(async () => ({ sid: 'test' })) }));

const ORIGIN = 'http://127.0.0.1:3000';
const SECRET = 'job-routes-secret-'.padEnd(48, 'q');
const SERVICE_TOKEN = 'service-token-'.padEnd(48, 'z');
let fetchMock;

beforeEach(() => {
  vi.stubEnv('SESSION_SECRET', SECRET);
  vi.stubEnv('OPERATOR_PASSWORD_HASH', 'configured');
  vi.stubEnv('BACKEND_URL', 'http://127.0.0.1:8000');
  vi.stubEnv('API_SERVICE_TOKEN', SERVICE_TOKEN);
  vi.stubEnv('APP_ORIGIN', '');
  fetchMock = vi.fn(async () => Response.json({ job: { id: 'j' }, created: true }, { status: 202 }));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function mutation(path, body, { session = true, csrf = true, origin = ORIGIN } = {}) {
  const { token, session: s } = createSessionToken(SECRET, 900);
  const headers = { host: '127.0.0.1:3000', 'content-type': 'application/json' };
  if (origin) headers.origin = origin;
  if (session) headers.cookie = `${SESSION_COOKIE_NAME}=${token}`;
  if (csrf) headers[CSRF_HEADER_NAME] = csrfTokenFor(s, SECRET);
  return new NextRequest(`${ORIGIN}${path}`, { method: 'POST', headers, body: typeof body === 'string' ? body : JSON.stringify(body) });
}

describe('POST /api/ingestion-jobs', () => {
  it('forwards ONLY the validated URL and graph flag, server-to-server with the service token', async () => {
    const response = await handleSubmitIngestion(
      mutation('/api/ingestion-jobs', { githubUrl: 'https://github.com/a/b', buildGraph: true, localPath: '/etc', target_path: '/tmp' }),
    );

    expect(response.status).toBe(202);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('http://127.0.0.1:8000/api/v1/ingestion-jobs');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ github_url: 'https://github.com/a/b', build_graph: true });
    expect(init.headers.Authorization).toBe(`Bearer ${SERVICE_TOKEN}`);
    expect(await response.text()).not.toContain(SERVICE_TOKEN);
  });

  it.each([
    ['no session', { session: false }, 401],
    ['no CSRF token', { csrf: false }, 403],
    ['cross-site origin', { origin: 'https://evil.example' }, 403],
    ['missing origin', { origin: null }, 403],
  ])('rejects %s without calling the backend', async (_label, options, status) => {
    const response = await handleSubmitIngestion(mutation('/api/ingestion-jobs', { githubUrl: 'https://github.com/a/b' }, options));
    expect(response.status).toBe(status);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ['non-JSON body', 'githubUrl=https://github.com/a/b'],
    ['array body', [1, 2]],
    ['missing URL', { buildGraph: false }],
    ['URL not a string', { githubUrl: ['https://github.com/a/b'] }],
    ['URL too long', { githubUrl: `https://github.com/a/${'b'.repeat(400)}` }],
    ['graph flag not boolean', { githubUrl: 'https://github.com/a/b', buildGraph: 'yes' }],
  ])('rejects %s with 400', async (_label, body) => {
    const response = await handleSubmitIngestion(mutation('/api/ingestion-jobs', body));
    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('passes through the backend error code and the existing repository id only', async () => {
    fetchMock.mockResolvedValueOnce(Response.json({ error: 'repository_already_ingested', repository_id: 7, extra: '/srv/x' }, { status: 409 }));
    const response = await handleSubmitIngestion(mutation('/api/ingestion-jobs', { githubUrl: 'https://github.com/a/b' }));
    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: 'repository_already_ingested', repositoryId: 7 });
  });

  it('reports an unreachable backend as a stable code', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('connect ECONNREFUSED 127.0.0.1:8000'));
    const response = await handleSubmitIngestion(mutation('/api/ingestion-jobs', { githubUrl: 'https://github.com/a/b' }));
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: 'backend_unreachable' });
  });
});

describe('POST /api/repositories/:id/reindex', () => {
  it('forwards a validated id and graph flag only', async () => {
    await handleReindex(mutation('/api/repositories/12/reindex', { buildGraph: false, rootPath: '/' }), '12');
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('http://127.0.0.1:8000/api/v1/repositories/12/reindex');
    expect(JSON.parse(init.body)).toEqual({ build_graph: false });
  });

  it.each(['0', '-1', 'abc', '1/../../x', '9'.repeat(30)])('rejects repository id %p', async (id) => {
    const response = await handleReindex(mutation(`/api/repositories/x/reindex`, {}), id);
    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('requires the CSRF token', async () => {
    expect((await handleReindex(mutation('/api/repositories/1/reindex', {}, { csrf: false }), '1')).status).toBe(403);
  });
});

describe('GET /api/ingestion-jobs/:id', () => {
  it('validates the id before calling the backend', async () => {
    expect((await handleGetJob('../../etc')).status).toBe(400);
    expect((await handleGetJob('ABCDEF00-0000-0000-0000-000000000000')).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('returns the backend job', async () => {
    fetchMock.mockResolvedValueOnce(Response.json({ id: '0b0b0b0b-0000-4000-8000-000000000000', status: 'running' }));
    const response = await handleGetJob('0b0b0b0b-0000-4000-8000-000000000000');
    expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('no-store');
  });
});
