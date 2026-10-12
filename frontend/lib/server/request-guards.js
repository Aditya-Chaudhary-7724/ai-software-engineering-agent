import 'server-only';

/**
 * Request-level guards for state-changing Route Handlers.
 *
 * CSRF defense is layered; any one layer failing rejects the request:
 * 1. The session cookie is SameSite=Strict (never attached cross-site).
 * 2. `isSameOriginRequest`: the browser-set `Origin` header (and, where sent,
 *    `Sec-Fetch-Site`) must match this app's own origin. Browsers do not let
 *    page scripts forge either header.
 * 3. Session-bound CSRF token in the `x-csrf-token` header (session.js) for
 *    every authenticated state-changing request.
 */

/**
 * The origin this request was addressed to. Production always pins it via
 * APP_ORIGIN (required there — see config.js), which also defeats DNS
 * rebinding. In development it is derived from the `Host` header the browser
 * sent, NOT from `request.url`: Next.js normalizes 127.0.0.1 to `localhost`
 * in `request.url`, which would make every real login from
 * http://127.0.0.1:3000 look cross-origin. A cross-site page cannot set the
 * `Host` of a request the victim's browser sends to this server.
 */
export function expectedOrigin(request, appOrigin) {
  if (appOrigin) return appOrigin;
  const { protocol, origin } = new URL(request.url);
  const host = request.headers.get('host');
  return host ? `${protocol}//${host}` : origin;
}

export function isSameOriginRequest(request, appOrigin) {
  const origin = request.headers.get('origin');
  if (!origin || origin !== expectedOrigin(request, appOrigin)) return false;
  const fetchSite = request.headers.get('sec-fetch-site');
  return fetchSite === null || fetchSite === 'same-origin';
}

const CLIENT_KEY_PATTERN = /^[0-9A-Fa-f:.]{2,45}$/;
export const UNTRUSTED_CLIENT_KEY = 'untrusted-client';

/**
 * Rate-limit identity. X-Forwarded-For is client-controlled unless a trusted
 * reverse proxy overwrites it, so it is only used when TRUST_PROXY_HEADERS is
 * set. Otherwise every request shares one bucket — strict, but correct for a
 * single-operator tool (a forged header can't buy extra attempts).
 */
export function clientKeyFor(request, trustProxyHeaders) {
  if (!trustProxyHeaders) return UNTRUSTED_CLIENT_KEY;
  const forwarded = request.headers.get('x-forwarded-for')?.split(',')[0]?.trim();
  return forwarded && CLIENT_KEY_PATTERN.test(forwarded) ? forwarded : UNTRUSTED_CLIENT_KEY;
}

/** Reads at most `maxBytes` of the body; returns `null` if it is larger. */
export async function readBodyWithLimit(request, maxBytes) {
  const declared = Number(request.headers.get('content-length') ?? '0');
  if (declared > maxBytes) return null;
  if (!request.body) return '';

  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  return Buffer.concat(chunks).toString('utf8');
}
