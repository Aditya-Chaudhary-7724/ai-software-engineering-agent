import { NextRequest } from 'next/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { handleAgentDecision, handleGetAgentTask, handleRoutePreview, handleSubmitAgentTask } from '@/lib/server/agent-task-routes';
import { CSRF_HEADER_NAME, SESSION_COOKIE_NAME, createSessionToken, csrfTokenFor } from '@/lib/server/session';

vi.mock('@/lib/server/dal', () => ({ requireSession: vi.fn(async () => ({ sid: 'test' })) }));

const ORIGIN = 'http://127.0.0.1:3000';
const SECRET = 'agent-routes-secret-'.padEnd(48, 'q');
const SERVICE_TOKEN = 'service-token-'.padEnd(48, 'z');
const TASK_ID = '0b9f5c2e-7d1a-4c3b-9e8f-1a2b3c4d5e6f';
const HASH = 'a'.repeat(64);
const INTERRUPT = 'b'.repeat(32);
let fetchMock;

beforeEach(() => {
  vi.stubEnv('SESSION_SECRET', SECRET);
  vi.stubEnv('OPERATOR_PASSWORD_HASH', 'configured');
  vi.stubEnv('BACKEND_URL', 'http://127.0.0.1:8000');
  vi.stubEnv('API_SERVICE_TOKEN', SERVICE_TOKEN);
  vi.stubEnv('APP_ORIGIN', '');
  fetchMock = vi.fn(async () => Response.json({ id: TASK_ID, status: 'queued' }, { status: 202 }));
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

const decisionPath = `/api/agent-tasks/${TASK_ID}/decision`;
const validDecision = { approved: true, interruptId: INTERRUPT, diffSha256: HASH };

describe('POST /api/agent-tasks/:id/decision', () => {
  it('forwards ONLY the validated boolean and binding, with the service token', async () => {
    const response = await handleAgentDecision(mutation(decisionPath, { ...validDecision, rootPath: '/etc', force: true }), TASK_ID);

    expect(response.status).toBe(202);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(`http://127.0.0.1:8000/api/v1/agent-tasks/${TASK_ID}/decision`);
    expect(JSON.parse(init.body)).toEqual({ approved: true, interrupt_id: INTERRUPT, diff_sha256: HASH });
    expect(init.headers.Authorization).toBe(`Bearer ${SERVICE_TOKEN}`);
    expect(await response.text()).not.toContain(SERVICE_TOKEN);
  });

  it('forwards a rejection as the boolean false', async () => {
    await handleAgentDecision(mutation(decisionPath, { ...validDecision, approved: false }), TASK_ID);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).approved).toBe(false);
  });

  it.each([['no'], ['yes'], ['true'], ['false'], [1], [0], [{}], [[]], [null]])(
    'refuses approved=%j without calling the backend',
    async (approved) => {
      const response = await handleAgentDecision(mutation(decisionPath, { ...validDecision, approved }), TASK_ID);
      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ error: 'invalid_decision' });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it.each([
    ['missing approved', { interruptId: INTERRUPT, diffSha256: HASH }],
    ['missing hash', { approved: true, interruptId: INTERRUPT }],
    ['missing interrupt', { approved: true, diffSha256: HASH }],
    ['short hash', { ...validDecision, diffSha256: 'abc' }],
    ['upper-case hash', { ...validDecision, diffSha256: HASH.toUpperCase() }],
    ['path-like interrupt', { ...validDecision, interruptId: '../x' }],
    ['array body', [validDecision]],
  ])('refuses %s', async (_label, body) => {
    const response = await handleAgentDecision(mutation(decisionPath, body), TASK_ID);
    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ['no session', { session: false }, 401],
    ['no CSRF token', { csrf: false }, 403],
    ['cross-site origin', { origin: 'https://evil.example' }, 403],
    ['missing origin', { origin: null }, 403],
  ])('rejects %s without calling the backend', async (_label, options, status) => {
    const response = await handleAgentDecision(mutation(decisionPath, validDecision, options), TASK_ID);
    expect(response.status).toBe(status);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('rejects a malformed task id', async () => {
    const response = await handleAgentDecision(mutation('/api/agent-tasks/x/decision', validDecision), '../../ingestion-jobs');
    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('passes through backend refusal codes only', async () => {
    fetchMock.mockResolvedValueOnce(Response.json({ error: 'approval_diff_mismatch', detail: '/srv/secret' }, { status: 409 }));
    const response = await handleAgentDecision(mutation(decisionPath, validDecision), TASK_ID);
    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: 'approval_diff_mismatch' });
  });
});

describe('POST /api/repositories/:id/agent-tasks', () => {
  it('forwards only the trimmed instruction', async () => {
    const response = await handleSubmitAgentTask(
      mutation('/api/repositories/3/agent-tasks', { instruction: '  Fix greet  ', root_path: '/etc', route: 'modify' }),
      '3',
    );
    expect(response.status).toBe(202);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('http://127.0.0.1:8000/api/v1/repositories/3/agent-tasks');
    expect(JSON.parse(init.body)).toEqual({ instruction: 'Fix greet' });
  });

  it.each([
    ['blank', { instruction: '   ' }],
    ['too long', { instruction: 'x'.repeat(2001) }],
    ['not a string', { instruction: ['fix'] }],
  ])('refuses a %s instruction', async (_label, body) => {
    const response = await handleSubmitAgentTask(mutation('/api/repositories/3/agent-tasks', body), '3');
    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('requires CSRF and a valid repository id', async () => {
    expect((await handleSubmitAgentTask(mutation('/api/repositories/3/agent-tasks', { instruction: 'x' }, { csrf: false }), '3')).status).toBe(403);
    expect((await handleSubmitAgentTask(mutation('/api/repositories/x/agent-tasks', { instruction: 'x' }), '../1')).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('route preview and task reads', () => {
  it('route preview is CSRF-guarded and forwards only the instruction', async () => {
    fetchMock.mockResolvedValueOnce(Response.json({ route: 'modify', supported: true, requires_approval: true }));
    const response = await handleRoutePreview(mutation('/api/agent-tasks/route-preview', { instruction: 'Fix it', extra: 1 }));
    expect(response.status).toBe(200);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ instruction: 'Fix it' });
    expect((await handleRoutePreview(mutation('/api/agent-tasks/route-preview', { instruction: 'x' }, { csrf: false }))).status).toBe(403);
  });

  it('GET validates the task id before calling the backend', async () => {
    expect((await handleGetAgentTask('not-a-uuid')).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockResolvedValueOnce(Response.json({ id: TASK_ID }));
    expect((await handleGetAgentTask(TASK_ID)).status).toBe(200);
  });
});
