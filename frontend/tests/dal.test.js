import { afterEach, describe, expect, it, vi } from 'vitest';

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
