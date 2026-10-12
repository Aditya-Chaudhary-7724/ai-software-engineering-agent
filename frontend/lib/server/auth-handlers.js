import 'server-only';

import { NextResponse } from 'next/server';

import { verifyPassword } from '../crypto/argon2id';
import { ConfigError, getAuthConfig } from './config';
import { getLoginRateLimiter } from './rate-limit';
import { clientKeyFor, isSameOriginRequest, readBodyWithLimit } from './request-guards';
import {
  CSRF_HEADER_NAME,
  SESSION_COOKIE_NAME,
  createSessionToken,
  sessionCookieOptions,
  verifyCsrfToken,
  verifySessionToken,
} from './session';

/**
 * Login/logout logic, kept out of the route files so tests can call it with
 * injected dependencies. Every failure returns a fixed `{ error: <code> }`:
 * the client learns WHAT kind of failure occurred (so the UI can say "too many
 * attempts"), never WHY in detail — in particular, never whether a password was
 * "close", and never which configuration value is missing.
 */

const MAX_LOGIN_BODY_BYTES = 4096;
const NO_STORE = { 'Cache-Control': 'no-store' };

const fail = (status, error, headers = {}) => NextResponse.json({ error }, { status, headers: { ...NO_STORE, ...headers } });

function loadConfig(logger) {
  try {
    return getAuthConfig();
  } catch (error) {
    if (!(error instanceof ConfigError)) throw error;
    logger.error(`[auth] login disabled: ${error.code}`); // which setting — server log only
    return null;
  }
}

export async function handleLogin(request, { limiter = getLoginRateLimiter(), verify = verifyPassword, logger = console } = {}) {
  const config = loadConfig(logger);
  if (!config) return fail(503, 'auth_not_configured');

  if (!isSameOriginRequest(request, config.appOrigin)) return fail(403, 'forbidden_origin');
  if (!(request.headers.get('content-type') ?? '').startsWith('application/json')) return fail(415, 'invalid_request');

  const raw = await readBodyWithLimit(request, MAX_LOGIN_BODY_BYTES);
  if (raw === null) return fail(413, 'invalid_request');
  let password;
  try {
    password = JSON.parse(raw)?.password;
  } catch {
    return fail(400, 'invalid_request');
  }
  if (typeof password !== 'string' || password.length === 0 || password.length > 1024) return fail(400, 'invalid_request');

  const clientKey = clientKeyFor(request, config.trustProxyHeaders);
  const decision = limiter.check(clientKey);
  if (!decision.allowed) {
    return fail(429, 'too_many_attempts', { 'Retry-After': String(decision.retryAfterSeconds) });
  }
  if (!limiter.tryAcquire()) return fail(429, 'too_many_attempts', { 'Retry-After': '5' });

  let valid;
  try {
    valid = await verify(password, config.passwordHash);
  } catch {
    logger.error('[auth] login disabled: OPERATOR_PASSWORD_HASH is not a valid Argon2id hash');
    return fail(503, 'auth_not_configured');
  } finally {
    limiter.release();
  }

  if (!valid) {
    limiter.recordFailure(clientKey);
    logger.warn('[auth] failed login attempt'); // never the submitted password
    return fail(401, 'invalid_credentials');
  }

  limiter.recordSuccess(clientKey);
  // A NEW session id on every login (no session fixation).
  const { token, session } = createSessionToken(config.sessionSecret, config.sessionMaxAgeSeconds);
  const response = NextResponse.json({ ok: true, expiresAt: session.exp }, { status: 200, headers: NO_STORE });
  response.cookies.set(SESSION_COOKIE_NAME, token, sessionCookieOptions(config.sessionMaxAgeSeconds));
  return response;
}

export async function handleLogout(request, { logger = console } = {}) {
  const config = loadConfig(logger);
  if (!config) return fail(503, 'auth_not_configured');
  if (!isSameOriginRequest(request, config.appOrigin)) return fail(403, 'forbidden_origin');

  const session = verifySessionToken(request.cookies.get(SESSION_COOKIE_NAME)?.value, config.sessionSecret);
  // With a live session, logout is a state-changing request like any other:
  // it requires the session-bound CSRF token. Without one there is nothing to
  // protect, and clearing a stale cookie is always safe.
  if (session && !verifyCsrfToken(session, config.sessionSecret, request.headers.get(CSRF_HEADER_NAME))) {
    return fail(403, 'csrf_token_invalid');
  }

  const response = NextResponse.json({ ok: true }, { status: 200, headers: NO_STORE });
  response.cookies.set(SESSION_COOKIE_NAME, '', { ...sessionCookieOptions(0), maxAge: 0 });
  return response;
}

/**
 * For FUTURE state-changing Route Handlers (F3+): one call that enforces the
 * full chain — valid session, same origin, session-bound CSRF token. Returns
 * `{ session }` or `{ response }` (an error response to return as-is).
 */
export function requireAuthenticatedMutation(request) {
  let config;
  try {
    config = getAuthConfig();
  } catch {
    return { response: fail(503, 'auth_not_configured') };
  }
  const session = verifySessionToken(request.cookies.get(SESSION_COOKIE_NAME)?.value, config.sessionSecret);
  if (!session) return { response: fail(401, 'unauthenticated') };
  if (!isSameOriginRequest(request, config.appOrigin)) return { response: fail(403, 'forbidden_origin') };
  if (!verifyCsrfToken(session, config.sessionSecret, request.headers.get(CSRF_HEADER_NAME))) {
    return { response: fail(403, 'csrf_token_invalid') };
  }
  return { session };
}
