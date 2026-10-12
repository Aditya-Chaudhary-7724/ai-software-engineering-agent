import { NextRequest } from 'next/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { encodeStoredHash, hashPassword } from '@/lib/crypto/argon2id';
import { handleLogin, handleLogout, requireAuthenticatedMutation } from '@/lib/server/auth-handlers';
import { createLoginRateLimiter } from '@/lib/server/rate-limit';
import { CSRF_HEADER_NAME, SESSION_COOKIE_NAME, createSessionToken, csrfTokenFor, verifySessionToken } from '@/lib/server/session';

const ORIGIN = 'http://127.0.0.1:3000';
const SECRET = 'test-session-secret-'.padEnd(48, 'x');
const PASSWORD = 'correct horse battery staple';
let storedHash;

const silentLogger = { error: vi.fn(), warn: vi.fn() };

function loginRequest(body, headers = {}) {
  return new NextRequest(`${ORIGIN}/api/auth/login`, {
    method: 'POST',
    headers: { host: '127.0.0.1:3000', origin: ORIGIN, 'content-type': 'application/json', ...headers },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });
}

function authedRequest(path, { token, headers = {} } = {}) {
  return new NextRequest(`${ORIGIN}${path}`, {
    method: 'POST',
    headers: { host: '127.0.0.1:3000', origin: ORIGIN, ...(token ? { cookie: `${SESSION_COOKIE_NAME}=${token}` } : {}), ...headers },
  });
}

beforeEach(async () => {
  storedHash ??= encodeStoredHash(await hashPassword(PASSWORD, { memory: 8192, passes: 1, parallelism: 1, tagLength: 32 }));
  vi.stubEnv('SESSION_SECRET', SECRET);
  vi.stubEnv('OPERATOR_PASSWORD_HASH', storedHash);
  vi.stubEnv('SESSION_MAX_AGE_SECONDS', '900');
  vi.stubEnv('TRUST_PROXY_HEADERS', '');
  vi.stubEnv('APP_ORIGIN', '');
  silentLogger.error.mockClear();
  silentLogger.warn.mockClear();
});

afterEach(() => vi.unstubAllEnvs());

const deps = (overrides = {}) => ({ limiter: createLoginRateLimiter(), logger: silentLogger, ...overrides });

describe('POST /api/auth/login', () => {
  it('sets a signed HttpOnly SameSite=Strict session cookie on the right password', async () => {
    const response = await handleLogin(loginRequest({ password: PASSWORD }), deps());

    expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('no-store');
    const setCookie = response.headers.get('set-cookie');
    expect(setCookie).toMatch(new RegExp(`^${SESSION_COOKIE_NAME}=`));
    expect(setCookie).toMatch(/HttpOnly/i);
    expect(setCookie).toMatch(/SameSite=strict/i);
    expect(setCookie).toMatch(/Path=\//);
    expect(setCookie).toMatch(/Max-Age=900/);

    const token = response.cookies.get(SESSION_COOKIE_NAME).value;
    const session = verifySessionToken(token, SECRET);
    expect(session).not.toBeNull();
    expect((await response.json()).expiresAt).toBe(session.exp);
  });

  it('returns a generic 401 for a wrong password, never echoing it', async () => {
    const response = await handleLogin(loginRequest({ password: 'wrong-password-value' }), deps());
    const text = await response.text();

    expect(response.status).toBe(401);
    expect(JSON.parse(text)).toEqual({ error: 'invalid_credentials' });
    expect(text).not.toContain('wrong-password-value');
    expect(response.headers.get('set-cookie')).toBeNull();
    for (const call of [...silentLogger.warn.mock.calls, ...silentLogger.error.mock.calls]) {
      expect(JSON.stringify(call)).not.toContain('wrong-password-value');
    }
  });

  it('rate-limits after repeated failures and stops verifying passwords entirely', async () => {
    const verify = vi.fn(async () => false);
    const limiter = createLoginRateLimiter({ perClientFailures: 3 });
    for (let i = 0; i < 3; i += 1) {
      expect((await handleLogin(loginRequest({ password: `guess-${i}` }), deps({ limiter, verify }))).status).toBe(401);
    }
    const blocked = await handleLogin(loginRequest({ password: PASSWORD }), deps({ limiter, verify }));

    expect(blocked.status).toBe(429);
    expect(await blocked.json()).toEqual({ error: 'too_many_attempts' });
    expect(Number(blocked.headers.get('retry-after'))).toBeGreaterThan(0);
    expect(verify).toHaveBeenCalledTimes(3); // the 4th attempt never reached Argon2
  });

  it('a forged X-Forwarded-For cannot buy extra attempts unless proxy headers are trusted', async () => {
    const limiter = createLoginRateLimiter({ perClientFailures: 2 });
    const verify = vi.fn(async () => false);
    for (let i = 0; i < 2; i += 1) {
      await handleLogin(loginRequest({ password: 'x' }, { 'x-forwarded-for': `198.51.100.${i}` }), deps({ limiter, verify }));
    }
    const third = await handleLogin(loginRequest({ password: 'x' }, { 'x-forwarded-for': '198.51.100.99' }), deps({ limiter, verify }));
    expect(third.status).toBe(429);
  });

  it.each([
    ['missing Origin', { origin: '' }],
    ['cross-site Origin', { origin: 'https://evil.example' }],
    ['cross-site fetch metadata', { 'sec-fetch-site': 'cross-site' }],
  ])('rejects %s with 403 before checking the password', async (_label, headers) => {
    const verify = vi.fn();
    const request = loginRequest({ password: PASSWORD }, headers);
    if (headers.origin === '') request.headers.delete('origin');

    const response = await handleLogin(request, deps({ verify }));

    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: 'forbidden_origin' });
    expect(verify).not.toHaveBeenCalled();
  });

  it.each([
    ['form-encoded body (classic CSRF form post)', 'password=x', { 'content-type': 'application/x-www-form-urlencoded' }, 415],
    ['malformed JSON', '{"password":', {}, 400],
    ['missing password', {}, {}, 400],
    ['non-string password', { password: 42 }, {}, 400],
    ['oversized body', JSON.stringify({ password: 'a'.repeat(5000) }), {}, 413],
  ])('rejects %s', async (_label, body, headers, status) => {
    const response = await handleLogin(loginRequest(body, headers), deps());
    expect(response.status).toBe(status);
    expect((await response.json()).error).toBe('invalid_request');
  });

  it.each([
    ['SESSION_SECRET missing', { SESSION_SECRET: '' }],
    ['SESSION_SECRET too short', { SESSION_SECRET: 'short' }],
    ['OPERATOR_PASSWORD_HASH missing', { OPERATOR_PASSWORD_HASH: '' }],
    ['OPERATOR_PASSWORD_HASH not Argon2id', { OPERATOR_PASSWORD_HASH: 'plaintext-password' }],
  ])('fails closed with a generic 503 when %s', async (_label, env) => {
    for (const [key, value] of Object.entries(env)) vi.stubEnv(key, value);

    const response = await handleLogin(loginRequest({ password: PASSWORD }), deps());
    const text = await response.text();

    expect(response.status).toBe(503);
    expect(JSON.parse(text)).toEqual({ error: 'auth_not_configured' });
    expect(text).not.toMatch(/SESSION_SECRET|OPERATOR_PASSWORD_HASH/);
    expect(response.headers.get('set-cookie')).toBeNull();
  });
});

describe('POST /api/auth/logout', () => {
  it('requires the session-bound CSRF token when a session exists', async () => {
    const { token } = createSessionToken(SECRET, 900);
    const response = await handleLogout(authedRequest('/api/auth/logout', { token }), { logger: silentLogger });
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: 'csrf_token_invalid' });
  });

  it('clears the cookie with a valid CSRF token', async () => {
    const { token, session } = createSessionToken(SECRET, 900);
    const response = await handleLogout(
      authedRequest('/api/auth/logout', { token, headers: { [CSRF_HEADER_NAME]: csrfTokenFor(session, SECRET) } }),
      { logger: silentLogger },
    );
    expect(response.status).toBe(200);
    expect(response.headers.get('set-cookie')).toMatch(new RegExp(`^${SESSION_COOKIE_NAME}=;.*Max-Age=0`, 'i'));
  });

  it('rejects a cross-origin logout', async () => {
    const response = await handleLogout(authedRequest('/api/auth/logout', { headers: { origin: 'https://evil.example' } }), { logger: silentLogger });
    expect(response.status).toBe(403);
  });

  it('clears a stale cookie without requiring a token', async () => {
    const expired = createSessionToken(SECRET, 900, Date.now() - 3600_000).token;
    const response = await handleLogout(authedRequest('/api/auth/logout', { token: expired }), { logger: silentLogger });
    expect(response.status).toBe(200);
  });
});

describe('requireAuthenticatedMutation (for future state-changing routes)', () => {
  it('passes only with session + same origin + matching CSRF token', () => {
    const { token, session } = createSessionToken(SECRET, 900);
    const csrf = csrfTokenFor(session, SECRET);

    expect(requireAuthenticatedMutation(authedRequest('/api/x', { token, headers: { [CSRF_HEADER_NAME]: csrf } })).session.sid).toBe(session.sid);
    expect(requireAuthenticatedMutation(authedRequest('/api/x', { headers: { [CSRF_HEADER_NAME]: csrf } })).response.status).toBe(401);
    expect(requireAuthenticatedMutation(authedRequest('/api/x', { token })).response.status).toBe(403);
    expect(
      requireAuthenticatedMutation(authedRequest('/api/x', { token, headers: { [CSRF_HEADER_NAME]: csrf, origin: 'https://evil.example' } })).response.status,
    ).toBe(403);
  });
});
