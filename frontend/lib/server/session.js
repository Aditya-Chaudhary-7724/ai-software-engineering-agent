import 'server-only';

import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto';

/**
 * Stateless, HMAC-SHA256-signed session tokens:  <payload b64url>.<mac b64url>
 * payload = { v: 1, sid, iat, exp }  (seconds since epoch)
 *
 * Why stateless (no session table): there is exactly one operator, and the
 * Next.js server may run as several instances/serverless invocations that
 * share no memory — a signed token needs no shared store. The tradeoff is
 * revocation: logout deletes the cookie, but a copied token stays valid until
 * `exp`. Mitigations: a bounded lifetime (SESSION_MAX_AGE_SECONDS, default
 * 8h), and rotating SESSION_SECRET invalidates every outstanding session.
 *
 * Two keys are derived from SESSION_SECRET with distinct labels, so a value
 * signed for one purpose (a CSRF token) can never verify as another (a session).
 */

export const SESSION_COOKIE_NAME = process.env.NODE_ENV === 'production' ? '__Host-asea_session' : 'asea_session';
export const CSRF_HEADER_NAME = 'x-csrf-token';

const TOKEN_VERSION = 1;
const CLOCK_SKEW_SECONDS = 60;
const SID_PATTERN = /^[A-Za-z0-9_-]{22}$/;
const MAX_TOKEN_LENGTH = 512;

function deriveKey(secret, label) {
  return createHmac('sha256', secret).update(`asea:${label}:v1`).digest();
}

function mac(secret, label, data) {
  return createHmac('sha256', deriveKey(secret, label)).update(data).digest();
}

function safeEqual(a, b) {
  return a.length === b.length && timingSafeEqual(a, b);
}

const nowSeconds = (nowMs) => Math.floor(nowMs / 1000);

export function createSessionToken(secret, maxAgeSeconds, nowMs = Date.now()) {
  const iat = nowSeconds(nowMs);
  const session = { v: TOKEN_VERSION, sid: randomBytes(16).toString('base64url'), iat, exp: iat + maxAgeSeconds };
  const payload = Buffer.from(JSON.stringify(session)).toString('base64url');
  const signature = mac(secret, 'session', payload).toString('base64url');
  return { token: `${payload}.${signature}`, session };
}

/** Returns the session payload if the token is authentic and unexpired, otherwise `null`. Never throws. */
export function verifySessionToken(token, secret, nowMs = Date.now()) {
  if (typeof token !== 'string' || token.length > MAX_TOKEN_LENGTH || !secret) return null;
  const parts = token.split('.');
  if (parts.length !== 2) return null;
  const [payload, signature] = parts;

  const expected = mac(secret, 'session', payload);
  if (!safeEqual(Buffer.from(signature, 'base64url'), expected)) return null;

  let session;
  try {
    session = JSON.parse(Buffer.from(payload, 'base64url').toString('utf8'));
  } catch {
    return null;
  }
  const now = nowSeconds(nowMs);
  const valid =
    session?.v === TOKEN_VERSION &&
    typeof session.sid === 'string' && SID_PATTERN.test(session.sid) &&
    Number.isInteger(session.iat) && Number.isInteger(session.exp) &&
    session.iat <= now + CLOCK_SKEW_SECONDS &&
    session.exp > now;
  return valid ? { v: session.v, sid: session.sid, iat: session.iat, exp: session.exp } : null;
}

/**
 * CSRF token bound to one session (synchronizer-token pattern, without
 * server state): HMAC(csrf-key, sid). A cross-site attacker can neither read
 * it (it is only ever rendered into this app's own pages) nor derive it.
 */
export function csrfTokenFor(session, secret) {
  return mac(secret, 'csrf', session.sid).toString('base64url');
}

export function verifyCsrfToken(session, secret, presented) {
  if (!session || typeof presented !== 'string' || presented.length > 128) return false;
  return safeEqual(Buffer.from(presented, 'base64url'), mac(secret, 'csrf', session.sid));
}

export function sessionCookieOptions(maxAgeSeconds) {
  return {
    httpOnly: true, // never readable by page JavaScript
    secure: process.env.NODE_ENV === 'production', // required by the __Host- prefix
    sameSite: 'strict', // never sent on cross-site requests
    path: '/',
    maxAge: maxAgeSeconds,
    priority: 'high',
  };
}
