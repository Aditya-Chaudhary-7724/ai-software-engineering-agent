import { afterEach, describe, expect, it, vi } from 'vitest';

import { ROLE_DEMO, SESSION_COOKIE_NAME, createSessionToken } from '@/lib/server/session';

const cookiesMock = vi.fn(async () => ({ get: () => undefined }));
vi.mock('next/headers', () => ({ cookies: cookiesMock }));
vi.mock('next/navigation', () => ({ redirect: vi.fn(() => { throw new Error('NEXT_REDIRECT'); }) }));

afterEach(() => {
  vi.unstubAllEnvs();
  cookiesMock.mockClear();
});

describe('DAL getSession', () => {
  it('regression: reads request cookies even when auth is misconfigured, so protected pages are never prerendered statically', async () => {
    vi.stubEnv('SESSION_SECRET', '');
    const { getSession } = await import('@/lib/server/dal');

    expect(await getSession()).toBeNull();
    expect(cookiesMock).toHaveBeenCalledTimes(1);
  });

  it('requireSession redirects to /login without a valid session', async () => {
    vi.stubEnv('SESSION_SECRET', 'x'.repeat(48));
    vi.stubEnv('OPERATOR_PASSWORD_HASH', 'configured');
    const { requireSession } = await import('@/lib/server/dal');
    await expect(requireSession()).rejects.toThrow('NEXT_REDIRECT');
  });
});

describe('DAL requireOperatorSession', () => {
  const SECRET = 'x'.repeat(48);
  const withCookie = (token) => cookiesMock.mockImplementationOnce(async () => ({ get: (name) => (name === SESSION_COOKIE_NAME ? { value: token } : undefined) }));

  it('sends a demo session away from operator-only pages', async () => {
    vi.stubEnv('SESSION_SECRET', SECRET);
    vi.stubEnv('OPERATOR_PASSWORD_HASH', 'configured');
    const { redirect } = await import('next/navigation');
    const { requireOperatorSession } = await import('@/lib/server/dal');
    withCookie(createSessionToken(SECRET, 900, Date.now(), ROLE_DEMO).token);
    await expect(requireOperatorSession()).rejects.toThrow('NEXT_REDIRECT');
    expect(redirect).toHaveBeenLastCalledWith('/');
  });

  it('lets the operator through', async () => {
    vi.stubEnv('SESSION_SECRET', SECRET);
    vi.stubEnv('OPERATOR_PASSWORD_HASH', 'configured');
    const { requireOperatorSession } = await import('@/lib/server/dal');
    withCookie(createSessionToken(SECRET, 900).token);
    expect((await requireOperatorSession()).role).toBe('operator');
  });
});
