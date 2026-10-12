import 'server-only';

import { unstable_rethrow } from 'next/navigation';
import { NextResponse } from 'next/server';

import { readBodyWithLimit } from './request-guards';

/** Shared Route Handler plumbing: fixed error bodies, backend passthrough, bounded JSON bodies. */

const NO_STORE = { 'Cache-Control': 'no-store' };

export const errorResponse = (status, code, extra = {}) =>
  NextResponse.json({ error: code, ...extra }, { status, headers: NO_STORE });

export function fromBackend(result) {
  if (result.ok) return NextResponse.json(result.data, { status: result.status, headers: NO_STORE });
  return errorResponse(result.status, result.error, result.repositoryId ? { repositoryId: result.repositoryId } : {});
}

/** A JSON object body of at most `maxBytes`, or null. */
export async function readJsonObject(request, maxBytes = 4096) {
  if (!(request.headers.get('content-type') ?? '').startsWith('application/json')) return null;
  const raw = await readBodyWithLimit(request, maxBytes);
  if (raw === null) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export const REPOSITORY_ID = /^[1-9]\d{0,17}$/;

/**
 * Wraps a Route Handler so an UNEXPECTED exception becomes a fixed JSON
 * error (`frontend_route_error`) instead of Next.js's empty-bodied 500.
 *
 * Why (F4 incident): a stale dev-server module made `api.ask` undefined;
 * the route threw a TypeError, Next returned 500 with no body, and the UI
 * could only report a misleading "backend_error" although the backend was
 * never called. The detail goes to the server log only — never to the
 * browser. Next's own control-flow errors (redirect, notFound, …) are
 * re-thrown untouched via `unstable_rethrow`.
 */
export function withRouteErrorBoundary(handler, logger = console) {
  return async function boundedRouteHandler(...args) {
    try {
      return await handler(...args);
    } catch (error) {
      unstable_rethrow(error);
      logger.error('[route] unhandled error in Route Handler', error);
      return errorResponse(500, 'frontend_route_error');
    }
  };
}
