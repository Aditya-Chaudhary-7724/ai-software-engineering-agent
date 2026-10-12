import 'server-only';

import { requireAuthenticatedMutation } from './auth-handlers';
import { api } from './backend';
import { REPOSITORY_ID, errorResponse as error, fromBackend, readJsonObject as readJson } from './route-helpers';

/**
 * Route Handler logic for F3 ingestion jobs (kept here so tests can call it
 * directly). The browser sends `{ githubUrl, buildGraph }` or nothing but a
 * repository id; this server validates the shapes, then builds the backend
 * request from those validated values alone — unknown fields (a path, a
 * workspace root) are never forwarded. Every mutation goes through
 * `requireAuthenticatedMutation`: session + same origin + CSRF token.
 */

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export async function handleSubmitIngestion(request) {
  const guard = requireAuthenticatedMutation(request);
  if (guard.response) return guard.response;
  const body = await readJson(request);
  if (!body || typeof body.githubUrl !== 'string' || body.githubUrl.length === 0 || body.githubUrl.length > 300) {
    return error(400, 'invalid_request');
  }
  if (body.buildGraph !== undefined && typeof body.buildGraph !== 'boolean') return error(400, 'invalid_request');
  return fromBackend(await api.submitIngestion({ githubUrl: body.githubUrl, buildGraph: body.buildGraph === true }));
}

export async function handleReindex(request, repositoryId) {
  const guard = requireAuthenticatedMutation(request);
  if (guard.response) return guard.response;
  if (!REPOSITORY_ID.test(repositoryId ?? '')) return error(400, 'invalid_request');
  const body = await readJson(request);
  if (!body || (body.buildGraph !== undefined && typeof body.buildGraph !== 'boolean')) return error(400, 'invalid_request');
  return fromBackend(await api.reindex(repositoryId, { buildGraph: body.buildGraph === true }));
}

export async function handleGetJob(jobId) {
  if (!UUID.test(jobId ?? '')) return error(400, 'invalid_job_id');
  return fromBackend(await api.job(jobId));
}
