import 'server-only';

import { cache } from 'react';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import { getAuthConfig } from './config';
import { SESSION_COOKIE_NAME, csrfTokenFor, verifySessionToken } from './session';

/**
 * Data Access Layer: the AUTHORITATIVE session check. `proxy.js` performs the
 * same verification as an early redirect, but per the Next.js guidance (and
 * the lesson of CVE-2025-29927, a middleware bypass) it is never the only
 * gate — every protected layout and every backend call verifies again here.
 */

export const getSession = cache(async () => {
  // Read the request cookies FIRST, unconditionally. This is what marks every
  // page using the DAL as dynamic (rendered per request). If configuration
  // were checked first and returned early, a build without SESSION_SECRET
  // would let Next.js prerender protected pages as static HTML.
  const cookieStore = await cookies();
  let config;
  try {
    config = getAuthConfig();
  } catch {
    return null; // misconfigured auth == nobody is authenticated (fail closed)
  }
  return verifySessionToken(cookieStore.get(SESSION_COOKIE_NAME)?.value, config.sessionSecret);
});

export async function requireSession() {
  const session = await getSession();
  if (!session) redirect('/login');
  return session;
}

/** For rendering into this app's own pages (e.g. the logout control). */
export async function getCsrfToken() {
  const session = await requireSession();
  return csrfTokenFor(session, getAuthConfig().sessionSecret);
}
