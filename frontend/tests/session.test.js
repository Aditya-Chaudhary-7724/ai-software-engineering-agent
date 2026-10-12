import { describe, expect, it } from 'vitest';

import {
  SESSION_COOKIE_NAME,
  createSessionToken,
  csrfTokenFor,
  sessionCookieOptions,
  verifyCsrfToken,
  verifySessionToken,
} from '@/lib/server/session';

const SECRET = 's'.repeat(48);
const OTHER_SECRET = 'o'.repeat(48);
const T0 = Date.UTC(2026, 9, 11, 12, 0, 0);

describe('session tokens', () => {
  it('round-trips a freshly created session', () => {
    const { token, session } = createSessionToken(SECRET, 3600, T0);
    expect(verifySessionToken(token, SECRET, T0)).toEqual(session);
    expect(session.exp - session.iat).toBe(3600);
  });

  it('issues a distinct session id every time (no fixation)', () => {
    const ids = new Set(Array.from({ length: 50 }, () => createSessionToken(SECRET, 60, T0).session.sid));
    expect(ids.size).toBe(50);
  });

  it('expires exactly at exp', () => {
    const { token } = createSessionToken(SECRET, 3600, T0);
    expect(verifySessionToken(token, SECRET, T0 + 3599_000)).not.toBeNull();
    expect(verifySessionToken(token, SECRET, T0 + 3600_000)).toBeNull();
  });

  it('rejects a token signed with a different secret (secret rotation revokes all sessions)', () => {
    const { token } = createSessionToken(SECRET, 3600, T0);
    expect(verifySessionToken(token, OTHER_SECRET, T0)).toBeNull();
  });

  it('rejects any modification of the payload (e.g. extending exp)', () => {
    const { token } = createSessionToken(SECRET, 3600, T0);
    const [payload, signature] = token.split('.');
    const forged = JSON.parse(Buffer.from(payload, 'base64url').toString());
    forged.exp += 10 * 365 * 24 * 3600;
    const forgedPayload = Buffer.from(JSON.stringify(forged)).toString('base64url');
    expect(verifySessionToken(`${forgedPayload}.${signature}`, SECRET, T0)).toBeNull();
  });

  it('rejects a tampered signature', () => {
    const { token } = createSessionToken(SECRET, 3600, T0);
    const flipped = token.slice(0, -2) + (token.at(-2) === 'A' ? 'B' : 'A') + token.at(-1);
    expect(verifySessionToken(flipped, SECRET, T0)).toBeNull();
  });

  it('rejects a token issued in the future beyond clock skew', () => {
    const { token } = createSessionToken(SECRET, 3600, T0 + 5 * 60_000);
    expect(verifySessionToken(token, SECRET, T0)).toBeNull();
  });

  it.each([undefined, null, '', 'abc', 'a.b.c', '.', 'x'.repeat(600), 123])('rejects malformed token %p', (token) => {
    expect(verifySessionToken(token, SECRET, T0)).toBeNull();
  });

  it('never verifies without a secret', () => {
    const { token } = createSessionToken(SECRET, 3600, T0);
    expect(verifySessionToken(token, '', T0)).toBeNull();
  });
});

describe('CSRF tokens', () => {
  it('accepts the token for its own session only', () => {
    const a = createSessionToken(SECRET, 3600, T0).session;
    const b = createSessionToken(SECRET, 3600, T0).session;
    const tokenA = csrfTokenFor(a, SECRET);
    expect(verifyCsrfToken(a, SECRET, tokenA)).toBe(true);
    expect(verifyCsrfToken(b, SECRET, tokenA)).toBe(false);
    expect(verifyCsrfToken(a, OTHER_SECRET, tokenA)).toBe(false);
  });

  it.each([undefined, null, '', 'nope', 'x'.repeat(200)])('rejects missing/garbage token %p', (presented) => {
    const session = createSessionToken(SECRET, 3600, T0).session;
    expect(verifyCsrfToken(session, SECRET, presented)).toBe(false);
  });

  it('uses a key separate from session signing (a CSRF token is not a session signature)', () => {
    const { token, session } = createSessionToken(SECRET, 3600, T0);
    const [payload] = token.split('.');
    expect(csrfTokenFor(session, SECRET)).not.toBe(token.split('.')[1]);
    expect(verifySessionToken(`${payload}.${csrfTokenFor(session, SECRET)}`, SECRET, T0)).toBeNull();
  });
});

describe('cookie options', () => {
  it('are HttpOnly, SameSite=Strict, path=/ with the configured lifetime', () => {
    expect(sessionCookieOptions(900)).toMatchObject({ httpOnly: true, sameSite: 'strict', path: '/', maxAge: 900 });
  });

  it('use a non-prefixed name outside production (tests run with NODE_ENV=test)', () => {
    expect(SESSION_COOKIE_NAME).toBe('asea_session');
  });
});
