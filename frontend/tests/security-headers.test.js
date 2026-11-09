import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

import { HSTS_VALUE, buildContentSecurityPolicy, createNonce, securityContext } from '@/lib/security-headers';
import { proxy } from '@/proxy';
import { SESSION_COOKIE_NAME, createSessionToken } from '@/lib/server/session';

const SECRET = 'csp-test-secret-'.padEnd(48, 'z');
const page = (path, token) =>
  new NextRequest(`http://localhost:3000${path}`, token ? { headers: { cookie: `${SESSION_COOKIE_NAME}=${token}` } } : {});
const nonceOf = (csp) => /'nonce-([^']+)'/.exec(csp)?.[1];

describe('content security policy (F8)', () => {
  it('allows scripts only by nonce: no unsafe-inline, no unsafe-eval in production', () => {
    const csp = buildContentSecurityPolicy({ nonce: 'AAAAAAAAAAAAAAAAAAAAAA==' });
    const scriptSrc = csp.split('; ').find((d) => d.startsWith('script-src'));
    expect(scriptSrc).toBe("script-src 'self' 'nonce-AAAAAAAAAAAAAAAAAAAAAA==' 'strict-dynamic'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("base-uri 'self'");
    expect(csp).not.toContain('upgrade-insecure-requests');
  });

  it("adds 'unsafe-eval' only in development and upgrade-insecure-requests only over https", () => {
    expect(buildContentSecurityPolicy({ nonce: 'AAAAAAAAAAAAAAAAAAAAAA==', development: true })).toContain("'unsafe-eval'");
    expect(buildContentSecurityPolicy({ nonce: 'AAAAAAAAAAAAAAAAAAAAAA==', https: true })).toContain('upgrade-insecure-requests');
  });

  it('refuses a nonce that could inject directives', () => {
    expect(() => buildContentSecurityPolicy({ nonce: "x'; script-src *" })).toThrow();
    expect(() => buildContentSecurityPolicy({ nonce: '' })).toThrow();
  });

  it('creates a fresh 128-bit nonce each time', () => {
    const a = createNonce();
    expect(Buffer.from(a, 'base64')).toHaveLength(16);
    expect(createNonce()).not.toBe(a);
  });

  it('treats only a production https APP_ORIGIN as https', () => {
    expect(securityContext({ NODE_ENV: 'production', APP_ORIGIN: 'https://lab.example.com' })).toEqual({ development: false, https: true });
    expect(securityContext({ NODE_ENV: 'production', APP_ORIGIN: 'http://127.0.0.1:3000' })).toEqual({ development: false, https: false });
    expect(securityContext({ NODE_ENV: 'development', APP_ORIGIN: 'https://lab.example.com' }).https).toBe(false);
  });
});

describe('proxy sets the policy on every response it produces (F8)', () => {
  beforeEach(() => {
    vi.stubEnv('SESSION_SECRET', SECRET);
    vi.stubEnv('OPERATOR_PASSWORD_HASH', '$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2g');
  });
  afterEach(() => vi.unstubAllEnvs());

  it('forwards the same nonce to Next.js (request header) that the browser receives', () => {
    const { token } = createSessionToken(SECRET, 3600);
    const response = proxy(page('/traces', token));
    const csp = response.headers.get('content-security-policy');
    expect(nonceOf(csp)).toBeTruthy();
    expect(response.headers.get('x-middleware-request-content-security-policy')).toBe(csp);
  });

  it('uses a different nonce per request', () => {
    const a = proxy(page('/login')).headers.get('content-security-policy');
    const b = proxy(page('/login')).headers.get('content-security-policy');
    expect(nonceOf(a)).not.toBe(nonceOf(b));
  });

  it('covers redirects and 401 responses too', () => {
    expect(proxy(page('/repositories')).headers.get('content-security-policy')).toContain("'strict-dynamic'");
    expect(proxy(page('/api/anything')).headers.get('content-security-policy')).toContain("'strict-dynamic'");
  });

  it('sends HSTS only for a production https origin', () => {
    expect(proxy(page('/login')).headers.get('strict-transport-security')).toBeNull();
    vi.stubEnv('NODE_ENV', 'production');
    vi.stubEnv('APP_ORIGIN', 'https://lab.example.com');
    expect(proxy(page('/login')).headers.get('strict-transport-security')).toBe(HSTS_VALUE);
  });
});
