import 'server-only';

/**
 * Server-only configuration. Every value here is read from `process.env` at
 * call time and validated; anything missing or weak FAILS CLOSED (throws a
 * ConfigError with a stable code) instead of falling back to a default.
 * There is deliberately no default password, hash, or secret anywhere.
 *
 * None of these variables uses the NEXT_PUBLIC_ prefix, so Next.js never
 * inlines them into browser bundles; `server-only` additionally makes the
 * BUILD fail if a client component ever imports this module.
 */

export const MIN_SECRET_LENGTH = 32;
export const DEFAULT_SESSION_MAX_AGE_SECONDS = 8 * 60 * 60;
const SESSION_MAX_AGE_BOUNDS = [5 * 60, 7 * 24 * 60 * 60];

export class ConfigError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

function parseSessionMaxAge(raw) {
  if (raw === undefined || raw === '') return DEFAULT_SESSION_MAX_AGE_SECONDS;
  const value = Number(raw);
  const [min, max] = SESSION_MAX_AGE_BOUNDS;
  if (!Number.isInteger(value) || value < min || value > max) throw new ConfigError('session_max_age_invalid');
  return value;
}

function parseOrigin(raw) {
  if (!raw) return null;
  try {
    const url = new URL(raw);
    if (!['http:', 'https:'].includes(url.protocol) || url.origin !== raw.replace(/\/$/, '')) throw new Error();
    return url.origin;
  } catch {
    throw new ConfigError('app_origin_invalid');
  }
}

export function getAuthConfig() {
  const sessionSecret = process.env.SESSION_SECRET ?? '';
  if (sessionSecret.length < MIN_SECRET_LENGTH) throw new ConfigError('session_secret_missing_or_weak');

  const passwordHash = process.env.OPERATOR_PASSWORD_HASH ?? '';
  if (!passwordHash) throw new ConfigError('operator_password_hash_missing');

  const appOrigin = parseOrigin(process.env.APP_ORIGIN);
  // Production must pin its public origin for CSRF origin checks (and to
  // reject DNS-rebinding requests carrying a hostile Host header).
  if (process.env.NODE_ENV === 'production' && !appOrigin) throw new ConfigError('app_origin_required_in_production');

  return {
    sessionSecret,
    passwordHash,
    sessionMaxAgeSeconds: parseSessionMaxAge(process.env.SESSION_MAX_AGE_SECONDS),
    // Only trust X-Forwarded-For when a known reverse proxy sets it;
    // otherwise any client could forge its own rate-limit identity.
    trustProxyHeaders: process.env.TRUST_PROXY_HEADERS === 'true',
    appOrigin,
  };
}

export function getBackendConfig() {
  let baseUrl;
  try {
    baseUrl = new URL(process.env.BACKEND_URL ?? '');
  } catch {
    throw new ConfigError('backend_url_invalid');
  }
  if (!['http:', 'https:'].includes(baseUrl.protocol)) throw new ConfigError('backend_url_invalid');

  const serviceToken = process.env.API_SERVICE_TOKEN ?? '';
  if (serviceToken.length < MIN_SECRET_LENGTH) throw new ConfigError('api_service_token_missing_or_weak');

  return { baseUrl, serviceToken };
}
