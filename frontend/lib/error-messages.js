/**
 * Stable error codes (from the backend API or the frontend's own server code)
 * mapped to operator-facing explanations. Raw exception text never reaches the
 * UI; an unknown code gets a generic message.
 */
const MESSAGES = {
  backend_unreachable: ['Backend unreachable', 'The FastAPI backend did not respond. Check that it is running and that BACKEND_URL points to it.'],
  frontend_backend_not_configured: ['Backend connection not configured', 'BACKEND_URL or API_SERVICE_TOKEN is missing or invalid in the frontend server environment.'],
  api_auth_not_configured: ['Backend API token not configured', 'The backend has no valid API_SERVICE_TOKEN, so /api/v1 refuses all requests.'],
  unauthorized: ['Backend rejected the service token', 'API_SERVICE_TOKEN differs between the frontend and the backend.'],
  database_not_configured: ['Database not configured', 'The backend has no DATABASE_URL.'],
  database_unavailable: ['Database unavailable', 'The backend cannot reach PostgreSQL right now.'],
  schema_not_initialized: ['Database schema not initialized', 'Run backend/scripts/apply_schema.py against the backend database.'],
  repository_not_found: ['Repository not found', 'No indexed repository has this id.'],
  repository_files_unavailable: ['Repository files unavailable', 'This repository is indexed, but its files are no longer present on the server (for example, a temporary evaluation checkout that was cleaned up).'],
  graph_not_configured: ['Knowledge graph not configured', 'Neo4j is optional and not configured on the backend.'],
  graph_unavailable: ['Knowledge graph unavailable', 'Neo4j is configured but not reachable right now.'],
  path_not_allowed: ['Path not allowed', 'That path resolves outside the repository.'],
  file_access_denied: ['File withheld', 'This file matches a sensitive-file pattern or lives in an ignored directory (.git, node_modules, …), so its content is never served.'],
  file_not_found: ['File not found', 'No such file in this repository.'],
  file_too_large: ['File too large', 'Files over 1 MB are not displayed.'],
  file_not_text: ['Binary file', 'This file is not UTF-8 text.'],
  invalid_trace_id: ['Invalid trace id', 'Trace ids are UUIDs.'],
  trace_not_found: ['Trace not found', 'No recorded trace has this id.'],
  trace_unreadable: ['Trace unreadable', 'The trace file exists but could not be parsed.'],
};

export function describeError(code) {
  const [title, detail] = MESSAGES[code] ?? ['Request failed', 'The backend returned an unexpected error.'];
  return { title, detail, code: code ?? 'unknown' };
}
