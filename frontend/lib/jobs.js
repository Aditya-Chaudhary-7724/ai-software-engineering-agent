/**
 * Pure helpers for ingestion-job state (shared by client components and
 * tests). Progress is shown ONLY as the stages the backend reports — no
 * estimated percentages are invented.
 */

import { SERVER_SIDE_MESSAGES } from './response-errors';

export const TERMINAL_STATUSES = new Set(['completed', 'failed']);
export const POLL_INTERVAL_MS = 2000;
export const MAX_POLL_FAILURES = 6;

export function isTerminal(job) {
  return TERMINAL_STATUSES.has(job?.status);
}

const STAGE_LABELS = {
  queued: 'Queued',
  fetching_metadata: 'Reading GitHub metadata',
  cloning: 'Cloning',
  indexing: 'Parsing & indexing',
  building_graph: 'Building knowledge graph',
  done: 'Done',
};

export function stageLabel(stage) {
  return STAGE_LABELS[stage] ?? stage;
}

/** The ordered stages this job will pass through. */
export function stagesFor(job) {
  const stages = job.job_type === 'ingest_github' ? ['queued', 'fetching_metadata', 'cloning', 'indexing'] : ['queued', 'indexing'];
  if (job.build_graph) stages.push('building_graph');
  stages.push('done');
  return stages;
}

/**
 * Per-stage state for display: 'done' | 'current' | 'failed' | 'pending'.
 * A failed job marks the stage it was in when it failed.
 */
export function stageStates(job) {
  const stages = stagesFor(job);
  const index = Math.max(0, stages.indexOf(job.stage));
  return stages.map((stage, i) => {
    let state = 'pending';
    if (job.status === 'completed') state = 'done';
    else if (i < index) state = 'done';
    else if (i === index) state = job.status === 'failed' ? 'failed' : 'current';
    return { stage, label: stageLabel(stage), state };
  });
}

/** Exponential backoff after failed polls: 2s, 4s, 8s … capped at 15s. */
export function nextPollDelay(consecutiveFailures) {
  if (consecutiveFailures <= 0) return POLL_INTERVAL_MS;
  return Math.min(15_000, POLL_INTERVAL_MS * 2 ** consecutiveFailures);
}

const GRAPH_TEXT = {
  not_requested: 'Not requested',
  pending: 'Requested — runs after indexing',
  completed: 'Built',
  failed: 'Requested, but it failed',
  not_run: 'Requested, but not run (the job failed earlier)',
};

export function graphOutcome(job) {
  return GRAPH_TEXT[job.graph_status] ?? job.graph_status;
}

const JOB_ERRORS = {
  ...SERVER_SIDE_MESSAGES,
  invalid_github_url: 'Not a valid GitHub repository URL. Use https://github.com/<owner>/<repo>.',
  invalid_request: 'The request was rejected. Check the form and try again.',
  repository_already_ingested: 'This repository is already indexed. Use Re-index on its page instead.',
  graph_not_configured: 'Knowledge-graph building is unavailable: Neo4j is not configured on the backend.',
  ingestion_not_configured: 'Ingestion is not configured on the backend (WORKSPACE_ROOT).',
  job_worker_unavailable: 'The backend’s background worker is not running, so the job cannot start.',
  github_repository_not_found: 'GitHub reports no such repository (or it is private and no token is configured).',
  github_rate_limited: 'GitHub refused the request (rate limit or access). Try again later.',
  github_api_error: 'GitHub returned an error for this repository.',
  github_unreachable: 'GitHub could not be reached from the backend.',
  clone_failed: 'Cloning the repository failed.',
  repository_too_large: 'The repository exceeds the configured size limit.',
  workspace_error: 'The server refused the clone destination.',
  indexing_failed: 'Parsing or indexing failed. The previous index, if any, is unchanged.',
  database_unavailable: 'The database was unavailable during the job.',
  worker_interrupted: 'The job was interrupted (the backend stopped while it ran). Submit it again.',
  repository_files_unavailable: 'The repository’s files are no longer on the server.',
  repository_not_found: 'Repository not found.',
  internal_error: 'The job failed unexpectedly. Details are in the backend log.',
  job_not_found: 'This job no longer exists.',
  unauthenticated: 'Your session has expired. Sign in again.',
  csrf_token_invalid: 'The request could not be verified. Reload the page and try again.',
  forbidden_origin: 'The request could not be verified. Reload the page and try again.',
  backend_unreachable: 'The backend is not reachable.',
  frontend_backend_not_configured: 'The frontend’s backend connection is not configured.',
};

export function jobErrorMessage(code) {
  return JOB_ERRORS[code] ?? 'The request failed.';
}

// Mirrors the backend's accepted form exactly, on the RAW string: the
// browser URL parser normalizes `..` segments and would be looser.
const GITHUB_URL = /^https:\/\/github\.com\/([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\/([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\/?$/;

/**
 * Client-side pre-check for fast feedback only; the backend's
 * `parse_github_url` is authoritative and re-validates everything.
 */
export function validateGithubUrl(input) {
  const raw = typeof input === 'string' ? input : '';
  if (/[\u0000-\u001f\u007f]/.test(raw)) return { ok: false, error: 'That URL contains invalid characters.' };
  const value = raw.trim();
  if (!value) return { ok: false, error: 'Enter a GitHub repository URL.' };
  if (value.length > 300) return { ok: false, error: 'That URL is too long.' };
  if (!/^https:\/\//i.test(value)) return { ok: false, error: 'Only https:// GitHub URLs are supported.' };
  if (/^https:\/\/[^/]*@/i.test(value)) return { ok: false, error: 'Remove credentials from the URL.' };
  if (!/^https:\/\/github\.com\//.test(value)) return { ok: false, error: 'Only github.com repositories are supported.' };
  const match = GITHUB_URL.exec(value.replace(/\.git\/?$/, ''));
  if (!match || match[1] === '..' || match[2] === '..') {
    return { ok: false, error: 'Use the repository root: https://github.com/<owner>/<repo>.' };
  }
  return { ok: true, fullName: `${match[1]}/${match[2]}`.toLowerCase() };
}
