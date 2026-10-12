/**
 * Regression tests for the F4 incident: a Route Handler that throws must
 * produce a stable JSON error the UI can explain — not Next.js's empty
 * 500 (which the Ask panel used to misreport as "backend_error").
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

import { NextRequest } from 'next/server';
import { redirect } from 'next/navigation';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { errorCodeFromResponse } from '@/lib/response-errors';
import { askErrorMessage } from '@/lib/ask';
import { jobErrorMessage } from '@/lib/jobs';
import { withRouteErrorBoundary } from '@/lib/server/route-helpers';
import { CSRF_HEADER_NAME, SESSION_COOKIE_NAME, createSessionToken, csrfTokenFor } from '@/lib/server/session';

describe('withRouteErrorBoundary', () => {
  it('turns an unexpected exception into a fixed JSON 500 and logs it server-side only', async () => {
    const logger = { error: vi.fn() };
    const handler = withRouteErrorBoundary(() => {
      throw new TypeError('secret detail: /srv/app/.env DATABASE_URL=postgres://u:p@h/db');
    }, logger);

    const response = await handler(new Request('http://x/api/y', { method: 'POST' }));
    const text = await response.text();

    expect(response.status).toBe(500);
    expect(JSON.parse(text)).toEqual({ error: 'frontend_route_error' });
    expect(text).not.toMatch(/secret|srv|DATABASE_URL|TypeError/);
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(logger.error).toHaveBeenCalledTimes(1);
  });

  it('passes normal responses through untouched', async () => {
    const handler = withRouteErrorBoundary(async () => Response.json({ ok: true }, { status: 202 }));
    const response = await handler();
    expect(response.status).toBe(202);
  });

  it('re-throws Next.js control-flow errors (redirect) instead of swallowing them', async () => {
    const handler = withRouteErrorBoundary(() => redirect('/login'), { error: vi.fn() });
    await expect(handler()).rejects.toThrow('NEXT_REDIRECT');
  });
});

describe('regression: Ask route when api.ask is missing (stale dev module)', () => {
  const SECRET = 'route-error-secret-'.padEnd(48, 'r');

  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv('SESSION_SECRET', SECRET);
    vi.stubEnv('OPERATOR_PASSWORD_HASH', 'configured');
    vi.stubEnv('APP_ORIGIN', '');
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.doUnmock('@/lib/server/backend');
  });

  it('answers 500 {"error":"frontend_route_error"} — not an empty body', async () => {
    // Exactly the observed failure: the route module loads, but the backend
    // client it imports has no `ask` function.
    vi.doMock('@/lib/server/backend', () => ({ api: {} }));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const { POST } = await import('@/app/api/repositories/[id]/ask/route.js');
    const { token, session } = createSessionToken(SECRET, 900);
    const request = new NextRequest('http://127.0.0.1:3000/api/repositories/13/ask', {
      method: 'POST',
      headers: {
        host: '127.0.0.1:3000', origin: 'http://127.0.0.1:3000', 'content-type': 'application/json',
        cookie: `${SESSION_COOKIE_NAME}=${token}`, [CSRF_HEADER_NAME]: csrfTokenFor(session, SECRET),
      },
      body: JSON.stringify({ question: 'Where is HTML escaping implemented?' }),
    });

    const response = await POST(request, { params: Promise.resolve({ id: '13' }) });

    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: 'frontend_route_error' });
  });
});

describe('errorCodeFromResponse (client attribution)', () => {
  it.each([
    [500, null, 'frontend_server_error'],                 // the original empty-bodied 500
    [500, {}, 'frontend_server_error'],
    [404, { detail: 'Not Found' }, 'unexpected_response'],
    [502, { error: 'answer_generation_failed' }, 'answer_generation_failed'],
    [500, { error: 'frontend_route_error' }, 'frontend_route_error'],
    [400, { error: '<script>alert(1)</script>' }, 'unexpected_response'],
  ])('status %s body %j -> %s', (status, body, code) => {
    expect(errorCodeFromResponse(status, body)).toBe(code);
  });

  it('never attributes an unknown failure to the backend', () => {
    for (const code of ['frontend_route_error', 'frontend_server_error']) {
      expect(askErrorMessage(code)).toMatch(/web app’s server/);
      expect(jobErrorMessage(code)).toMatch(/web app’s server/);
      expect(askErrorMessage(code)).not.toMatch(/backend/i);
    }
  });
});

describe('every Route Handler is wrapped in the error boundary (static guard)', () => {
  const appDir = new URL('../app', import.meta.url).pathname;
  const routeFiles = (dir) =>
    readdirSync(dir).flatMap((name) => {
      const full = join(dir, name);
      return statSync(full).isDirectory() ? routeFiles(full) : name === 'route.js' ? [full] : [];
    });

  it('finds the route files', () => {
    expect(routeFiles(appDir).length).toBeGreaterThanOrEqual(6);
  });

  it.each(routeFiles(new URL('../app', import.meta.url).pathname).map((f) => [relative(appDir, f), f]))('%s', (_name, file) => {
    const text = readFileSync(file, 'utf8');
    expect(text).toMatch(/withRouteErrorBoundary\(/);
    expect(text).not.toMatch(/export async function (GET|POST|PUT|PATCH|DELETE)\b/);
  });
});
