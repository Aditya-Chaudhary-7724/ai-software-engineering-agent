import { NextRequest } from 'next/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { agentTaskErrorMessage, changeSummary, isTerminal, proposalStateLabel, shouldPoll, statusLabel } from '@/lib/agent-tasks';
import { askErrorMessage } from '@/lib/ask';
import { DEMO_BANNER_TEXT, demoLimitMessage, formatWait, retryAfterFrom, splitExamples } from '@/lib/demo';
import { handleAgentDecision, handleSubmitAgentTask } from '@/lib/server/agent-task-routes';
import { handleAsk } from '@/lib/server/ask-route';
import { handleDemoLogin } from '@/lib/server/auth-handlers';
import { DEMO_SESSION_MAX_AGE_SECONDS, getDemoConfig } from '@/lib/server/config';
import { handleReindex, handleSubmitIngestion } from '@/lib/server/job-routes';
import { fromBackend } from '@/lib/server/route-helpers';
import {
  CSRF_HEADER_NAME,
  ROLE_DEMO,
  SESSION_COOKIE_NAME,
  createSessionToken,
  csrfTokenFor,
  verifySessionToken,
} from '@/lib/server/session';
import { proxy } from '@/proxy';

// The backend client reads the session through the DAL and the visitor's
// headers through next/headers; both are controlled per test.
const sessionState = vi.hoisted(() => ({ current: { sid: 'test' } }));
const headerState = vi.hoisted(() => ({ current: new Headers() }));
vi.mock('@/lib/server/dal', () => ({ requireSession: vi.fn(async () => sessionState.current) }));
vi.mock('next/headers', () => ({ headers: vi.fn(async () => headerState.current) }));

const ORIGIN = 'http://127.0.0.1:3000';
const SECRET = 'demo-mode-secret-'.padEnd(48, 'd');
const SERVICE_TOKEN = 'operator-token-'.padEnd(48, 'o');
const DEMO_TOKEN = 'demo-token-'.padEnd(48, 'p');
const silentLogger = { error: vi.fn(), warn: vi.fn() };
let fetchMock;

beforeEach(() => {
  vi.stubEnv('SESSION_SECRET', SECRET);
  vi.stubEnv('OPERATOR_PASSWORD_HASH', 'configured');
  vi.stubEnv('BACKEND_URL', 'http://127.0.0.1:8000');
  vi.stubEnv('API_SERVICE_TOKEN', SERVICE_TOKEN);
  vi.stubEnv('DEMO_API_SERVICE_TOKEN', DEMO_TOKEN);
  vi.stubEnv('APP_ORIGIN', '');
  vi.stubEnv('TRUST_PROXY_HEADERS', 'true');
  sessionState.current = { sid: 'test' };
  headerState.current = new Headers();
  fetchMock = vi.fn(async () => Response.json({ ok: true }));
  vi.stubGlobal('fetch', fetchMock);
  silentLogger.error.mockClear();
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const demoSession = () => createSessionToken(SECRET, 900, Date.now(), ROLE_DEMO);

function post(path, { session, body = {}, origin = ORIGIN } = {}) {
  const headers = { host: '127.0.0.1:3000', 'content-type': 'application/json' };
  if (origin) headers.origin = origin;
  if (session) {
    headers.cookie = `${SESSION_COOKIE_NAME}=${session.token}`;
    headers[CSRF_HEADER_NAME] = csrfTokenFor(session.session, SECRET);
  }
  return new NextRequest(`${ORIGIN}${path}`, { method: 'POST', headers, body: JSON.stringify(body) });
}

describe('session role', () => {
  it('signs the role into the token, and a token without one is the operator (pre-demo tokens)', () => {
    const demo = demoSession();
    expect(verifySessionToken(demo.token, SECRET).role).toBe('demo');
    expect(verifySessionToken(createSessionToken(SECRET, 900).token, SECRET).role).toBe('operator');
  });

  it('cannot be changed by editing the payload, and an unknown role is refused', () => {
    const { token } = demoSession();
    const [payload, mac] = token.split('.');
    const edited = JSON.parse(Buffer.from(payload, 'base64url').toString());
    edited.role = 'operator';
    expect(verifySessionToken(`${Buffer.from(JSON.stringify(edited)).toString('base64url')}.${mac}`, SECRET)).toBeNull();
    expect(() => createSessionToken(SECRET, 900, Date.now(), 'admin')).toThrow(TypeError);
  });
});

describe('getDemoConfig (fails closed)', () => {
  it('is off when unset, weak, or equal to the operator token', () => {
    vi.stubEnv('DEMO_API_SERVICE_TOKEN', '');
    expect(getDemoConfig()).toEqual({ enabled: false, problem: null });
    vi.stubEnv('DEMO_API_SERVICE_TOKEN', 'short');
    expect(getDemoConfig()).toMatchObject({ enabled: false, problem: 'demo_api_service_token_weak' });
    vi.stubEnv('DEMO_API_SERVICE_TOKEN', SERVICE_TOKEN);
    expect(getDemoConfig()).toMatchObject({ enabled: false, problem: 'demo_token_equals_operator_token' });
  });

  it('is on for a strong, distinct token', () => {
    expect(getDemoConfig()).toEqual({ enabled: true, demoToken: DEMO_TOKEN, problem: null });
  });
});

describe('POST /api/auth/demo', () => {
  it('issues a short demo session without any password', async () => {
    const response = await handleDemoLogin(post('/api/auth/demo'), { logger: silentLogger });
    expect(response.status).toBe(200);
    const cookie = response.cookies.get(SESSION_COOKIE_NAME);
    expect(cookie.maxAge).toBe(DEMO_SESSION_MAX_AGE_SECONDS);
    expect(cookie.httpOnly).toBe(true);
    expect(verifySessionToken(cookie.value, SECRET).role).toBe('demo');
  });

  it('refuses a cross-site request', async () => {
    const response = await handleDemoLogin(post('/api/auth/demo', { origin: 'https://evil.example' }), { logger: silentLogger });
    expect(response.status).toBe(403);
    expect(response.cookies.get(SESSION_COOKIE_NAME)).toBeUndefined();
  });

  it('answers 404 when demo mode is off, and logs why only on the server', async () => {
    vi.stubEnv('DEMO_API_SERVICE_TOKEN', SERVICE_TOKEN);
    const response = await handleDemoLogin(post('/api/auth/demo'), { logger: silentLogger });
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: 'demo_not_available' });
    expect(silentLogger.error).toHaveBeenCalledWith(expect.stringContaining('demo_token_equals_operator_token'));
  });

  it('is reachable without a session (proxy public path)', () => {
    const response = proxy(new NextRequest(`${ORIGIN}/api/auth/demo`, { method: 'POST' }));
    expect(response.headers.get('x-middleware-next')).toBe('1');
  });
});

describe('operator-only actions refuse a demo session without calling the backend', () => {
  it.each([
    ['ingestion', () => handleSubmitIngestion(post('/api/ingestion-jobs', { session: demoSession(), body: { githubUrl: 'https://github.com/a/b' } }))],
    ['re-index', () => handleReindex(post('/api/repositories/3/reindex', { session: demoSession(), body: {} }), '3')],
    [
      'approval decision',
      () =>
        handleAgentDecision(
          post('/api/agent-tasks/x/decision', { session: demoSession(), body: { approved: true, interruptId: 'ab', diffSha256: 'a'.repeat(64) } }),
          '0f8fad5b-d9cb-469f-a165-70867728950e',
        ),
    ],
  ])('%s', async (_name, call) => {
    const response = await call();
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: 'demo_forbidden' });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('backend client: the token follows the signed session role', () => {
  it('operator: operator token, no visitor address', async () => {
    await handleAsk(post('/api/repositories/3/ask', { session: createSessionToken(SECRET, 900), body: { question: 'q' } }), '3');
    const init = fetchMock.mock.calls[0][1];
    expect(init.headers.Authorization).toBe(`Bearer ${SERVICE_TOKEN}`);
    expect(init.headers['X-Demo-Client-IP']).toBeUndefined();
  });

  it('demo: demo token and the proxy-reported visitor address', async () => {
    sessionState.current = { sid: 'test', role: 'demo' };
    headerState.current = new Headers({ 'x-forwarded-for': '198.51.100.1, 203.0.113.7' });
    await handleAsk(post('/api/repositories/3/ask', { session: demoSession(), body: { question: 'q' } }), '3');
    const init = fetchMock.mock.calls[0][1];
    expect(init.headers.Authorization).toBe(`Bearer ${DEMO_TOKEN}`);
    expect(init.headers['X-Demo-Client-IP']).toBe('203.0.113.7');
  });

  it('demo without trusted proxy headers: every visitor shares one bucket', async () => {
    vi.stubEnv('TRUST_PROXY_HEADERS', '');
    sessionState.current = { sid: 'test', role: 'demo' };
    headerState.current = new Headers({ 'x-forwarded-for': '203.0.113.7' });
    await handleSubmitAgentTask(post('/api/repositories/3/agent-tasks', { session: demoSession(), body: { instruction: 'fix it' } }), '3');
    expect(fetchMock.mock.calls[0][1].headers['X-Demo-Client-IP']).toBe('0.0.0.0');
  });

  it('demo session but demo mode turned off: never falls back to the operator token', async () => {
    vi.stubEnv('DEMO_API_SERVICE_TOKEN', '');
    sessionState.current = { sid: 'test', role: 'demo' };
    const response = await handleAsk(post('/api/repositories/3/ask', { session: demoSession(), body: { question: 'q' } }), '3');
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: 'demo_not_available' });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('passes a demo limit refusal through with its retry time', async () => {
    sessionState.current = { sid: 'test', role: 'demo' };
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ error: 'demo_rate_limited', retry_after_seconds: 42 }, { status: 429 })));
    const response = await handleAsk(post('/api/repositories/3/ask', { session: demoSession(), body: { question: 'q' } }), '3');
    expect(response.status).toBe(429);
    expect(response.headers.get('retry-after')).toBe('42');
    expect(await response.json()).toEqual({ error: 'demo_rate_limited', retryAfterSeconds: 42 });
  });

  it('fromBackend adds no retry field when the backend sent none', async () => {
    const response = fromBackend({ ok: false, status: 404, error: 'repository_not_found' });
    expect(await response.json()).toEqual({ error: 'repository_not_found' });
    expect(response.headers.get('retry-after')).toBeNull();
  });
});

describe('friendly demo messages', () => {
  it('formats waits', () => {
    expect(formatWait(5)).toBe('a few seconds');
    expect(formatWait(61)).toBe('about 2 minutes');
    expect(formatWait(60)).toBe('about 1 minute');
    expect(formatWait(3 * 3600)).toBe('about 3 hours');
    expect(formatWait(null)).toBeNull();
  });

  it('every limit message says when to retry and points to the examples', () => {
    const rate = demoLimitMessage('demo_rate_limited', 120);
    expect(rate).toContain('about 2 minutes');
    expect(rate).toContain('pre-generated examples');
    const daily = demoLimitMessage('demo_daily_limit_reached', 7200);
    expect(daily).toContain('about 2 hours');
    expect(daily).toContain('pre-generated examples');
    expect(demoLimitMessage('demo_task_token_limit_reached')).toContain('pre-generated examples');
    expect(demoLimitMessage('repository_not_found')).toBeNull();
  });

  it('Ask and task errors prefer the demo message', () => {
    expect(askErrorMessage('demo_rate_limited', 30)).toContain('a few seconds');
    expect(agentTaskErrorMessage('demo_daily_limit_reached')).toContain('midnight UTC');
    expect(askErrorMessage('invalid_question')).toContain('Enter a question');
  });

  it('reads retry seconds only from a positive integer', () => {
    expect(retryAfterFrom({ retryAfterSeconds: 9 })).toBe(9);
    expect(retryAfterFrom({ retryAfterSeconds: '9' })).toBeNull();
    expect(retryAfterFrom({ retryAfterSeconds: -1 })).toBeNull();
    expect(retryAfterFrom(null)).toBeNull();
  });

  it('the banner says what was agreed', () => {
    expect(DEMO_BANNER_TEXT).toBe('Live demo, read-only, daily limits');
  });
});

describe('propose-only demo tasks in the task UI', () => {
  const task = { status: 'proposed', route: 'modify', outcome: {}, proposal: { state: 'display_only' } };

  it('is terminal, not polled, and says nothing was applied', () => {
    expect(isTerminal(task)).toBe(true);
    expect(shouldPoll(task)).toBe(false);
    expect(statusLabel('proposed')).toContain('display only');
    const summary = changeSummary(task);
    expect(summary.title).toContain('never applied');
    expect(summary.text).toContain('Nobody can approve');
    expect(proposalStateLabel('display_only')).toContain('never applied');
  });
});

describe('splitExamples', () => {
  it('separates Q&As from the task diff and ignores malformed items', () => {
    const { asks, task } = splitExamples([
      { kind: 'ask', prompt: 'a' },
      { kind: 'task', prompt: 't', diff: '--- a\n+++ b\n' },
      { kind: 'ask' },
      null,
    ]);
    expect(asks.map((e) => e.prompt)).toEqual(['a']);
    expect(task.prompt).toBe('t');
    expect(splitExamples(undefined)).toEqual({ asks: [], task: null });
  });
});
