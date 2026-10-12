import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

import { NextRequest } from 'next/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { proxy } from '@/proxy';
import { ConfigError, getAuthConfig, getBackendConfig } from '@/lib/server/config';
import { SESSION_COOKIE_NAME, createSessionToken } from '@/lib/server/session';

vi.mock('@/lib/server/dal', () => ({ requireSession: vi.fn(async () => ({ sid: 'test' })) }));

const SECRET = 'proxy-test-secret-'.padEnd(48, 'y');
const SERVICE_TOKEN = 'service-token-'.padEnd(48, 'z');

beforeEach(() => {
  vi.stubEnv('SESSION_SECRET', SECRET);
  vi.stubEnv('OPERATOR_PASSWORD_HASH', 'configured');
  vi.stubEnv('BACKEND_URL', 'http://127.0.0.1:8000');
  vi.stubEnv('API_SERVICE_TOKEN', SERVICE_TOKEN);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const page = (path, token) =>
  new NextRequest(`http://127.0.0.1:3000${path}`, { headers: token ? { cookie: `${SESSION_COOKIE_NAME}=${token}` } : {} });

describe('proxy (first gate)', () => {
  it('redirects an unauthenticated page request to /login, preserving the destination', () => {
    const response = proxy(page('/repositories/3?path=src%2Fa.py'));
    expect(response.status).toBe(307);
    const location = new URL(response.headers.get('location'));
    expect(location.pathname).toBe('/login');
    expect(location.searchParams.get('next')).toBe('/repositories/3?path=src%2Fa.py');
  });

  it('answers an unauthenticated /api request with 401 JSON, not a redirect', async () => {
    const response = proxy(page('/api/anything'));
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ error: 'unauthenticated' });
  });

  it('lets a valid session through and rejects a tampered or expired one', () => {
    const { token } = createSessionToken(SECRET, 900);
    expect(proxy(page('/traces', token)).headers.get('x-middleware-next')).toBe('1');
    expect(proxy(page('/traces', token.slice(0, -3) + 'AAA')).status).toBe(307);
    const expired = createSessionToken(SECRET, 900, Date.now() - 3600_000).token;
    expect(proxy(page('/traces', expired)).status).toBe(307);
  });

  it('treats everyone as unauthenticated when auth is misconfigured (fail closed)', () => {
    const { token } = createSessionToken(SECRET, 900);
    vi.stubEnv('SESSION_SECRET', '');
    expect(proxy(page('/', token)).status).toBe(307);
  });

  it('keeps login reachable, and sends an authenticated operator away from it', () => {
    expect(proxy(page('/login')).headers.get('x-middleware-next')).toBe('1');
    const { token } = createSessionToken(SECRET, 900);
    expect(new URL(proxy(page('/login', token)).headers.get('location')).pathname).toBe('/');
  });
});

describe('config validation (no defaults for secrets)', () => {
  it.each([
    ['SESSION_SECRET', '', 'session_secret_missing_or_weak'],
    ['SESSION_SECRET', 'x'.repeat(31), 'session_secret_missing_or_weak'],
    ['OPERATOR_PASSWORD_HASH', '', 'operator_password_hash_missing'],
    ['SESSION_MAX_AGE_SECONDS', '10', 'session_max_age_invalid'],
    ['SESSION_MAX_AGE_SECONDS', 'forever', 'session_max_age_invalid'],
    ['APP_ORIGIN', 'https://lab.example.com/path', 'app_origin_invalid'],
    ['NODE_ENV', 'production', 'app_origin_required_in_production'],
  ])('%s=%p -> %s', (key, value, code) => {
    vi.stubEnv(key, value);
    expect(() => getAuthConfig()).toThrow(new ConfigError(code));
  });

  it.each([
    ['API_SERVICE_TOKEN', 'short', 'api_service_token_missing_or_weak'],
    ['BACKEND_URL', 'not a url', 'backend_url_invalid'],
    ['BACKEND_URL', 'file:///etc/passwd', 'backend_url_invalid'],
  ])('%s=%p -> %s', (key, value, code) => {
    vi.stubEnv(key, value);
    expect(() => getBackendConfig()).toThrow(new ConfigError(code));
  });
});

describe('backend client (server-to-server only)', () => {
  it('sends the service token as a bearer header to /api/v1 and returns data', async () => {
    const fetchMock = vi.fn(async () => Response.json({ repositories: [], total: 0 }));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('@/lib/server/backend');

    const result = await api.repositories(10, 0);

    expect(result).toEqual({ ok: true, status: 200, data: { repositories: [], total: 0 } });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('http://127.0.0.1:8000/api/v1/repositories?limit=10&offset=0');
    expect(init.headers.Authorization).toBe(`Bearer ${SERVICE_TOKEN}`);
    expect(init.redirect).toBe('error');
  });

  it('encodes ids so a crafted id cannot escape the API path', async () => {
    const fetchMock = vi.fn(async () => Response.json({}));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('@/lib/server/backend');

    await api.trace('../../health/ready');

    expect(String(fetchMock.mock.calls[0][0])).toBe('http://127.0.0.1:8000/api/v1/traces/..%2F..%2Fhealth%2Fready');
  });

  it('maps backend errors to stable codes and never forwards raw bodies', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ error: 'repository_not_found' }, { status: 404 })));
    const { api } = await import('@/lib/server/backend');
    expect(await api.repository(9)).toEqual({ ok: false, status: 404, error: 'repository_not_found' });

    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>Traceback: secret</html>', { status: 500 })));
    expect(await api.repository(9)).toEqual({ ok: false, status: 500, error: 'backend_error' });

    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('connect ECONNREFUSED 127.0.0.1:8000'); }));
    expect(await api.repository(9)).toEqual({ ok: false, status: 503, error: 'backend_unreachable' });
  });

  it('refuses to call the backend when the service token is not configured', async () => {
    vi.stubEnv('API_SERVICE_TOKEN', '');
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('@/lib/server/backend');
    expect(await api.capabilities()).toEqual({ ok: false, status: 503, error: 'frontend_backend_not_configured' });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('secret exposure guards (static)', () => {
  const root = new URL('..', import.meta.url).pathname;
  const sourceFiles = (dir) =>
    readdirSync(dir).flatMap((name) => {
      const full = join(dir, name);
      if (['node_modules', '.next', 'tests'].includes(name)) return [];
      return statSync(full).isDirectory() ? sourceFiles(full) : /\.(m?js|jsx)$/.test(name) ? [full] : [];
    });

  it('no source file references a NEXT_PUBLIC_ variable', () => {
    for (const file of sourceFiles(root)) {
      expect(readFileSync(file, 'utf8'), relative(root, file)).not.toMatch(/process\.env\.NEXT_PUBLIC_|env\[['"]NEXT_PUBLIC_/);
    }
  });

  it('every lib/server module is marked server-only, and no client component imports one', () => {
    for (const file of sourceFiles(root)) {
      const text = readFileSync(file, 'utf8');
      const rel = relative(root, file);
      if (rel.startsWith('lib/server/')) expect(text, rel).toMatch(/^import 'server-only';/);
      if (/^['"]use client['"]/.test(text)) expect(text, rel).not.toMatch(/lib\/server\//);
    }
  });
});
