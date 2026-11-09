import { NextResponse } from 'next/server';

import { getAuthConfig } from '@/lib/server/config';
import { SESSION_COOKIE_NAME, verifySessionToken } from '@/lib/server/session';
import { applySecurityHeaders, buildContentSecurityPolicy, createNonce, securityContext } from '@/lib/security-headers';

/**
 * First gate (Next.js 16 "proxy", formerly middleware; runs on Node.js).
 * Redirects unauthenticated page requests to /login and rejects
 * unauthenticated /api requests with 401, before any route renders.
 *
 * NOT the only gate: protected layouts and the backend client verify the
 * session again (lib/server/dal.js), so a proxy bypass alone exposes nothing.
 */

const PUBLIC_PATHS = new Set(['/login', '/api/auth/login', '/api/auth/logout']);

function currentSession(request) {
  try {
    const { sessionSecret } = getAuthConfig();
    return verifySessionToken(request.cookies.get(SESSION_COOKIE_NAME)?.value, sessionSecret);
  } catch {
    return null;
  }
}

/**
 * F8: the nonce-bearing CSP goes on the forwarded REQUEST headers (where
 * Next.js reads the nonce for the scripts it renders) and on the response.
 */
function passThrough(request, headerContext) {
  const headers = new Headers(request.headers);
  headers.set('Content-Security-Policy', headerContext.csp);
  return applySecurityHeaders(NextResponse.next({ request: { headers } }), headerContext);
}

export function proxy(request) {
  const { pathname, search } = request.nextUrl;
  const session = currentSession(request);
  const { development, https } = securityContext();
  const headerContext = { csp: buildContentSecurityPolicy({ nonce: createNonce(), development, https }), https };

  if (PUBLIC_PATHS.has(pathname)) {
    if (pathname === '/login' && session) return applySecurityHeaders(NextResponse.redirect(new URL('/', request.url)), headerContext);
    return passThrough(request, headerContext);
  }
  if (session) return passThrough(request, headerContext);

  if (pathname.startsWith('/api/')) {
    return applySecurityHeaders(
      NextResponse.json({ error: 'unauthenticated' }, { status: 401, headers: { 'Cache-Control': 'no-store' } }),
      headerContext,
    );
  }
  const loginUrl = new URL('/login', request.url);
  if (pathname !== '/') loginUrl.searchParams.set('next', pathname + search);
  return applySecurityHeaders(NextResponse.redirect(loginUrl), headerContext);
}

export const config = {
  // Everything except Next's own static assets.
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
