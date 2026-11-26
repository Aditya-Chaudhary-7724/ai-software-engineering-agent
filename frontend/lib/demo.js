/**
 * Pure helpers for the public demo (shared by client components and tests).
 *
 * Demo limits protect a free AI quota, so hitting one is expected. Every
 * limit message says when to try again and points to the pre-generated
 * examples, which answer instantly and use no quota.
 */

export const DEMO_BANNER_TEXT = 'Live demo, read-only, daily limits';

/** "about 3 minutes" / "about 2 hours" / "a few seconds", from seconds. */
export function formatWait(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  if (seconds < 60) return 'a few seconds';
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `about ${minutes} minute${minutes === 1 ? '' : 's'}`;
  const hours = Math.round(minutes / 60);
  return `about ${hours} hour${hours === 1 ? '' : 's'}`;
}

const EXAMPLES_HINT = 'Meanwhile, the pre-generated examples on this page answer instantly and do not use the demo quota.';

/**
 * Friendly text for a demo refusal, or null when `code` is not one.
 * `retryAfterSeconds` comes from the backend's 429 (never guessed).
 */
export function demoLimitMessage(code, retryAfterSeconds) {
  const wait = formatWait(retryAfterSeconds);
  switch (code) {
    case 'demo_rate_limited':
      return `The live demo is busy: it allows only a few live requests per visitor and per minute to stay within a free AI quota. ${wait ? `Try again in ${wait}. ` : 'Try again shortly. '}${EXAMPLES_HINT}`;
    case 'demo_daily_limit_reached':
      return `The live demo has used today’s free AI allowance. ${wait ? `It resets in ${wait} (midnight UTC). ` : 'It resets at midnight UTC. '}${EXAMPLES_HINT}`;
    case 'demo_task_token_limit_reached':
      return `This demo task needed more AI tokens than one demo task may use, so it stopped before proposing a change. Try a smaller change to a short file. ${EXAMPLES_HINT}`;
    case 'demo_forbidden':
      return 'The public demo is read-only: adding or re-indexing repositories, approving changes and viewing traces need the operator sign-in.';
    case 'demo_proposal_not_approvable':
      return 'Demo proposals are display-only: nobody can approve or apply them.';
    case 'demo_not_available':
      return 'The public demo is not enabled on this server.';
    case 'demo_client_ip_required':
      return 'The demo could not identify your connection for its usage limits. Reload the page and try again.';
    default:
      return null;
  }
}

/** Retry-After seconds from one of this app's JSON error bodies, or null. */
export function retryAfterFrom(body) {
  const value = body?.retryAfterSeconds;
  return Number.isInteger(value) && value > 0 ? value : null;
}

/** Splits a repository's examples into Q&As (ordered) and the task diff. */
export function splitExamples(examples) {
  const list = Array.isArray(examples) ? examples : [];
  return {
    asks: list.filter((e) => e?.kind === 'ask' && typeof e.prompt === 'string'),
    task: list.find((e) => e?.kind === 'task' && typeof e.diff === 'string') ?? null,
  };
}
