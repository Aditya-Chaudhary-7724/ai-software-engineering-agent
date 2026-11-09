/**
 * F8: per-request security headers set by proxy.js.
 *
 * Content-Security-Policy with a fresh nonce per request. Next.js reads the
 * policy from the REQUEST header proxy.js forwards, extracts the nonce and
 * puts it on every script it renders (its bootstrap and inline RSC payload
 * scripts), so `script-src` needs neither 'unsafe-inline' nor a host list:
 * an injected <script> without the nonce does not run. 'strict-dynamic' lets
 * those trusted scripts load the app's own chunks.
 *
 * Deliberate allowances:
 * - style-src 'unsafe-inline': a few components set `style={...}`, which
 *   server rendering emits as style="" attributes; a nonce cannot cover
 *   attributes. Injected CSS cannot run script.
 * - 'unsafe-eval' in development ONLY: React's dev tooling needs eval.
 * - upgrade-insecure-requests and HSTS only when the app is served over
 *   HTTPS (APP_ORIGIN is https://): on a plain-http local production run
 *   they would break every subresource / pin the browser to https.
 *
 * Every page is rendered per request already (the DAL reads cookies), so a
 * per-request nonce costs no static optimisation.
 */

export const HSTS_VALUE = 'max-age=31536000';

export function createNonce() {
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return Buffer.from(bytes).toString('base64');
}

export function buildContentSecurityPolicy({ nonce, development = false, https = false }) {
  if (typeof nonce !== 'string' || !/^[A-Za-z0-9+/=]{16,}$/.test(nonce)) throw new Error('invalid CSP nonce');
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${development ? " 'unsafe-eval'" : ''}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  if (https) directives.push('upgrade-insecure-requests');
  return directives.join('; ');
}

/** What the current process serves: from NODE_ENV and the pinned APP_ORIGIN. */
export function securityContext(env = process.env) {
  return {
    development: env.NODE_ENV !== 'production',
    https: env.NODE_ENV === 'production' && typeof env.APP_ORIGIN === 'string' && env.APP_ORIGIN.startsWith('https://'),
  };
}

/** Sets the response headers; returns the response for chaining. */
export function applySecurityHeaders(response, { csp, https }) {
  response.headers.set('Content-Security-Policy', csp);
  if (https) response.headers.set('Strict-Transport-Security', HSTS_VALUE);
  return response;
}
