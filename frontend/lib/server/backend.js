import 'server-only';

import { ConfigError, getBackendConfig } from './config';
import { requireSession } from './dal';

/**
 * The ONLY place the frontend talks to the FastAPI backend. Server-to-server:
 * API_SERVICE_TOKEN is attached here and never leaves this process. Pages are
 * server components that call these helpers directly, so there is no generic
 * browser-reachable proxy route that could be abused to reach arbitrary
 * backend paths.
 *
 * Returns `{ ok: true, status, data }` or `{ ok: false, status, error }`, where
 * `error` is the backend's stable code (e.g. "repository_not_found") or one
 * of this module's own codes — never a raw exception message.
 */

const API_PREFIX = '/api/v1';
const TIMEOUT_MS = 10_000;

function buildUrl(baseUrl, path, query) {
  const url = new URL(API_PREFIX + path, baseUrl);
  // Defense in depth: a path can never redirect the request to another host.
  if (url.origin !== baseUrl.origin || !url.pathname.startsWith(API_PREFIX + '/')) {
    throw new Error('backend path escaped the API prefix');
  }
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, String(value));
  }
  return url;
}

async function backendRequest(method, path, { query, body } = {}) {
  await requireSession();

  let config;
  try {
    config = getBackendConfig();
  } catch (error) {
    if (error instanceof ConfigError) return { ok: false, status: 503, error: 'frontend_backend_not_configured' };
    throw error;
  }

  let response;
  try {
    response = await fetch(buildUrl(config.baseUrl, path, query), {
      method,
      headers: {
        Authorization: `Bearer ${config.serviceToken}`,
        Accept: 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      cache: 'no-store',
      redirect: 'error',
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    return { ok: false, status: 503, error: 'backend_unreachable' };
  }

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // Non-JSON body: fall through to a generic error below.
  }
  if (response.ok && payload !== null) return { ok: true, status: response.status, data: payload };
  const code = typeof payload?.error === 'string' && /^[a-z_]{1,64}$/.test(payload.error) ? payload.error : 'backend_error';
  const failure = { ok: false, status: response.status, error: code };
  // The only structured detail the backend ever adds to an error.
  if (Number.isInteger(payload?.repository_id)) failure.repositoryId = payload.repository_id;
  return failure;
}

export function backendGet(path, query) {
  return backendRequest('GET', path, { query });
}

export function backendPost(path, body) {
  return backendRequest('POST', path, { body });
}

export const api = {
  capabilities: () => backendGet('/capabilities'),
  repositories: (limit = 50, offset = 0) => backendGet('/repositories', { limit, offset }),
  repository: (id) => backendGet(`/repositories/${encodeURIComponent(id)}`),
  fileTree: (id) => backendGet(`/repositories/${encodeURIComponent(id)}/files`),
  fileContent: (id, path) => backendGet(`/repositories/${encodeURIComponent(id)}/files/content`, { path }),
  graphSummary: (id) => backendGet(`/repositories/${encodeURIComponent(id)}/graph/summary`),
  traces: (limit = 25, offset = 0) => backendGet('/traces', { limit, offset }),
  trace: (id) => backendGet(`/traces/${encodeURIComponent(id)}`),
  // F3: ingestion jobs. Bodies are built here from validated values only —
  // never by forwarding what the browser sent.
  jobs: ({ repositoryId, active, limit = 20 } = {}) =>
    backendGet('/ingestion-jobs', { repository_id: repositoryId, active: active ? 'true' : undefined, limit }),
  job: (id) => backendGet(`/ingestion-jobs/${encodeURIComponent(id)}`),
  submitIngestion: ({ githubUrl, buildGraph }) => backendPost('/ingestion-jobs', { github_url: githubUrl, build_graph: buildGraph }),
  reindex: (id, { buildGraph }) => backendPost(`/repositories/${encodeURIComponent(id)}/reindex`, { build_graph: buildGraph }),
  // F4: read-only Q&A.
  ask: (id, question) => backendPost(`/repositories/${encodeURIComponent(id)}/ask`, { question }),
  // F5: bounded graph exploration (the backend enforces the limits too).
  graphNeighborhood: (id, { node, limit } = {}) =>
    backendGet(`/repositories/${encodeURIComponent(id)}/graph/neighborhood`, { node, limit }),
  graphSearch: (id, q, limit = 20) => backendGet(`/repositories/${encodeURIComponent(id)}/graph/search`, { q, limit }),
};
