import { NextRequest } from 'next/server';
import { describe, expect, it } from 'vitest';

import { createLoginRateLimiter } from '@/lib/server/rate-limit';
import {
  UNTRUSTED_CLIENT_KEY,
  clientKeyFor,
  isSameOriginRequest,
  readBodyWithLimit,
} from '@/lib/server/request-guards';
import { safeNextPath } from '@/lib/safe-redirect';

const req = (headers = {}, body, url = 'http://127.0.0.1:3000/api/auth/login') =>
  new Request(url, { method: 'POST', headers: { host: new URL(url).host, ...headers }, body });

describe('isSameOriginRequest', () => {
  it('accepts a matching Origin', () => {
    expect(isSameOriginRequest(req({ origin: 'http://127.0.0.1:3000' }))).toBe(true);
    expect(isSameOriginRequest(req({ origin: 'http://127.0.0.1:3000', 'sec-fetch-site': 'same-origin' }))).toBe(true);
  });

  it.each([
    ['missing Origin', {}],
    ['cross-site Origin', { origin: 'https://evil.example' }],
    ['different port', { origin: 'http://127.0.0.1:3001' }],
    ['scheme downgrade', { origin: 'https://127.0.0.1:3000' }],
    ['null Origin (sandboxed iframe)', { origin: 'null' }],
    ['Sec-Fetch-Site cross-site', { origin: 'http://127.0.0.1:3000', 'sec-fetch-site': 'cross-site' }],
    ['Sec-Fetch-Site same-site', { origin: 'http://127.0.0.1:3000', 'sec-fetch-site': 'same-site' }],
  ])('rejects %s', (_label, headers) => {
    expect(isSameOriginRequest(req(headers))).toBe(false);
  });

  it('regression: works on 127.0.0.1 although NextRequest normalizes request.url to localhost', () => {
    const request = new NextRequest('http://127.0.0.1:3000/api/auth/login', {
      method: 'POST',
      headers: { host: '127.0.0.1:3000', origin: 'http://127.0.0.1:3000' },
    });
    expect(new URL(request.url).hostname).toBe('localhost'); // the trap
    expect(isSameOriginRequest(request)).toBe(true);
  });

  it('a request whose Host differs from its Origin (e.g. DNS rebinding) is rejected', () => {
    expect(isSameOriginRequest(req({ host: 'evil.example', origin: 'http://127.0.0.1:3000' }))).toBe(false);
  });

  it('uses APP_ORIGIN when configured (behind a reverse proxy)', () => {
    const internal = req({ origin: 'https://lab.example.com' }, undefined, 'http://10.0.0.5:3000/api/auth/login');
    expect(isSameOriginRequest(internal, 'https://lab.example.com')).toBe(true);
    expect(isSameOriginRequest(internal)).toBe(false);
  });
});

describe('clientKeyFor', () => {
  it('ignores X-Forwarded-For unless proxy headers are trusted', () => {
    const request = req({ 'x-forwarded-for': '203.0.113.9' });
    expect(clientKeyFor(request, false)).toBe(UNTRUSTED_CLIENT_KEY);
    expect(clientKeyFor(request, true)).toBe('203.0.113.9');
  });

  it('uses only the first hop and rejects garbage', () => {
    expect(clientKeyFor(req({ 'x-forwarded-for': '198.51.100.1, 10.0.0.1' }), true)).toBe('198.51.100.1');
    expect(clientKeyFor(req({ 'x-forwarded-for': '<script>' }), true)).toBe(UNTRUSTED_CLIENT_KEY);
  });
});

describe('readBodyWithLimit', () => {
  it('returns the body under the limit and null over it', async () => {
    expect(await readBodyWithLimit(req({}, 'a'.repeat(100)), 100)).toBe('a'.repeat(100));
    expect(await readBodyWithLimit(req({}, 'a'.repeat(101)), 100)).toBeNull();
  });

  it('rejects early on a large declared Content-Length', async () => {
    expect(await readBodyWithLimit(req({ 'content-length': '999999' }, 'small'), 100)).toBeNull();
  });
});

describe('login rate limiter', () => {
  const clock = (start = 0) => {
    let t = start;
    return { now: () => t, advance: (ms) => (t += ms) };
  };

  it('blocks a client after N failures, with Retry-After, until the window passes', () => {
    const c = clock();
    const limiter = createLoginRateLimiter({ perClientFailures: 3, windowMs: 60_000, now: c.now });
    for (let i = 0; i < 3; i += 1) {
      expect(limiter.check('a').allowed).toBe(true);
      limiter.recordFailure('a');
    }
    const blocked = limiter.check('a');
    expect(blocked).toEqual({ allowed: false, retryAfterSeconds: 60 });
    expect(limiter.check('b').allowed).toBe(true);
    c.advance(60_000);
    expect(limiter.check('a').allowed).toBe(true);
  });

  it('a successful login clears that client’s failures', () => {
    const limiter = createLoginRateLimiter({ perClientFailures: 2 });
    limiter.recordFailure('a');
    limiter.recordSuccess('a');
    limiter.recordFailure('a');
    expect(limiter.check('a').allowed).toBe(true);
  });

  it('enforces a global cap across rotating client keys', () => {
    const limiter = createLoginRateLimiter({ perClientFailures: 100, globalFailures: 5 });
    for (let i = 0; i < 5; i += 1) limiter.recordFailure(`client-${i}`);
    expect(limiter.check('fresh-client').allowed).toBe(false);
  });

  it('caps concurrent password verifications', () => {
    const limiter = createLoginRateLimiter({ maxConcurrent: 2 });
    expect(limiter.tryAcquire()).toBe(true);
    expect(limiter.tryAcquire()).toBe(true);
    expect(limiter.tryAcquire()).toBe(false);
    limiter.release();
    expect(limiter.tryAcquire()).toBe(true);
  });

  it('bounds memory: tracks at most maxTrackedClients keys', () => {
    const limiter = createLoginRateLimiter({ perClientFailures: 1, globalFailures: 1e9, maxTrackedClients: 3 });
    for (const key of ['a', 'b', 'c', 'd']) limiter.recordFailure(key);
    expect(limiter.check('a').allowed).toBe(true); // oldest evicted
    expect(limiter.check('d').allowed).toBe(false);
  });
});

describe('safeNextPath (open-redirect protection)', () => {
  it.each([
    ['/repositories/3?path=src%2Fa.py', '/repositories/3?path=src%2Fa.py'],
    ['/traces', '/traces'],
  ])('keeps same-site path %s', (input, expected) => {
    expect(safeNextPath(input)).toBe(expected);
  });

  it.each([
    'https://evil.example', '//evil.example', '/\\evil.example', '\\\\evil.example', 'javascript:alert(1)',
    '/login', '/api/auth/logout', '', null, 'relative', '/a\u0000b',
  ])('falls back for %p', (input) => {
    expect(safeNextPath(input)).toBe('/');
  });
});
