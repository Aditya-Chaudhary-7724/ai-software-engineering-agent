import { NextResponse } from 'next/server';

import { getAuthConfig } from '@/lib/server/config';
import { SESSION_COOKIE_NAME, verifySessionToken } from '@/lib/server/session';

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

export function proxy(request) {
  const { pathname, search } = request.nextUrl;
  const session = currentSession(request);

  if (PUBLIC_PATHS.has(pathname)) {
    if (pathname === '/login' && session) return NextResponse.redirect(new URL('/', request.url));
    return NextResponse.next();
  }
  if (session) return NextResponse.next();

  if (pathname.startsWith('/api/')) {
    return NextResponse.json({ error: 'unauthenticated' }, { status: 401, headers: { 'Cache-Control': 'no-store' } });
  }
  const loginUrl = new URL('/login', request.url);
  if (pathname !== '/') loginUrl.searchParams.set('next', pathname + search);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  // Everything except Next's own static assets.
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
