/**
 * Pure helpers for F6 agent tasks (shared by client components and tests).
 *
 * The approval rule lives in `approvalGate`: the UI enables Approve/Reject
 * only when the task is awaiting approval, its proposal is still pending
 * and unexpired, and the SHA-256 the BROWSER computed over the diff it is
 * displaying equals the hash the server reported. That computed hash — not
 * an echo of the server's — is what the decision sends, so an approval is
 * bound to the bytes the reviewer actually saw. The backend independently
 * re-verifies everything; this is the UI's half, never the only check.
 */

import { SERVER_SIDE_MESSAGES } from './response-errors';

export const POLL_INTERVAL_MS = 2000;
export const MAX_POLL_FAILURES = 6;
export const MAX_INSTRUCTION_CHARS = 2000;

const POLLING_STATUSES = new Set(['queued', 'running']);
export const TERMINAL_STATUSES = new Set(['completed', 'rejected', 'failed', 'expired', 'interrupted']);

export function shouldPoll(task) {
  return POLLING_STATUSES.has(task?.status);
}

export function isTerminal(task) {
  return TERMINAL_STATUSES.has(task?.status);
}

const STATUS = {
  queued: ['Queued', 'idle'],
  running: ['Running', 'running'],
  awaiting_approval: ['Awaiting your approval', 'awaiting_approval'],
  completed: ['Completed', 'completed'],
  rejected: ['Rejected', 'idle'],
  failed: ['Failed', 'failed'],
  expired: ['Approval expired', 'warn'],
  interrupted: ['Interrupted', 'interrupted'],
};

export function statusLabel(status) {
  return STATUS[status]?.[0] ?? status;
}

export function statusTone(status) {
  return STATUS[status]?.[1] ?? 'idle';
}

export const ROUTE_TEXT = {
  answer: 'Answer — retrieves code and generates text. Nothing is written.',
  modify: 'Modify — proposes a single-file diff, then waits for your explicit approval. Nothing is written without it.',
  test: 'Test — standalone sandboxed test runs are not available in this phase (planned for F7).',
};

/**
 * The one sentence that must be unambiguous: was anything written?
 * Derived only from fields the backend reported.
 */
export function changeSummary(task) {
  const applied = task.outcome?.changes_applied ?? 0;
  const written = applied === 1 ? '1 approved change was written' : `${applied} approved changes were written`;
  switch (task.status) {
    case 'queued':
    case 'running':
      return task.decided_at
        ? { tone: 'info', title: 'Processing your decision', text: 'The agent is continuing from your decision.' }
        : { tone: 'info', title: 'Working', text: 'No change has been proposed yet.' };
    case 'awaiting_approval':
      return {
        tone: 'warn',
        title: 'Proposed change — NOT applied',
        text: applied
          ? `${written} earlier in this task. The diff below is a new, unapplied proposal.`
          : 'Nothing has been written. The file changes only if you approve this exact diff.',
      };
    case 'completed':
      if (applied > 0) return { tone: 'ok', title: 'Applied', text: `${written} to the server checkout. Nothing was committed or pushed.` };
      if (task.outcome?.approved === true) {
        return { tone: 'warn', title: 'Approved, but not applied', text: 'The change could not be applied (for example, the file changed after the proposal). Nothing was written.' };
      }
      return { tone: 'info', title: 'Completed', text: task.route === 'modify' ? 'No change was applied.' : 'Read-only task: nothing was written.' };
    case 'rejected':
      return applied
        ? { tone: 'warn', title: 'Fix attempt rejected', text: `${written} earlier in this task and remains applied; the rejected fix was not.` }
        : { tone: 'info', title: 'Rejected', text: 'You rejected the proposal. Nothing was written.' };
    case 'expired':
      return { tone: 'warn', title: 'Approval expired', text: `The proposal was not decided in time and can no longer be applied.${applied ? ` ${written} earlier in this task.` : ' Nothing was written by it.'}` };
    case 'interrupted':
      return {
        tone: 'warn',
        title: 'Interrupted',
        text: task.decided_at
          ? 'The backend stopped while processing a decision. If you had approved, the change may already be in the file — inspect it before submitting again.'
          : 'The backend stopped while the agent was running. No approval had been given.',
      };
    case 'failed': {
      // A task can fail AFTER an approved change was written (e.g. the
      // sandbox or database errored during the post-apply step). The
      // failure path records only an error code, so `changes_applied` may
      // not include that write: say so instead of implying nothing changed.
      const approvedProposal = task.proposal?.state === 'approved';
      const caveat = applied
        ? ` ${written} earlier in this task.`
        : approvedProposal
          ? ' You had approved a change, so it may already be in the file — inspect it before submitting again.'
          : '';
      return { tone: 'err', title: 'Failed', text: `${agentTaskErrorMessage(task.error_code)}${caveat}` };
    }
    default:
      return { tone: 'info', title: statusLabel(task.status), text: '' };
  }
}

const TEST_SKIPPED_TEXT = {
  sandbox_disabled: 'not run: sandbox disabled in this deployment',
  sandbox_unavailable: 'not run: sandbox unavailable',
  no_test_command: 'not run: no supported test command',
};

/**
 * Sandbox test result for the task summary, or null when tests were never
 * reached (e.g. an answer, or a rejected change). Only `test_ran` with
 * `test_passed === true` reads as passed; every "not run" is a warning,
 * never a pass.
 */
export function testOutcome(outcome) {
  if (outcome?.test_ran) {
    return outcome.test_passed === true ? { text: 'passed', tone: 'ok' } : { text: 'failed', tone: 'err' };
  }
  const reason = outcome?.test_skipped_reason;
  if (!reason) return null;
  return { text: TEST_SKIPPED_TEXT[reason] ?? 'not run', tone: 'warn' };
}

export function proposalStateLabel(state) {
  return (
    {
      pending: 'Proposed — not applied',
      approved: 'Approved by you',
      rejected: 'Rejected by you — not applied',
      expired: 'Expired — not applied',
      closed: 'Closed — not applied',
    }[state] ?? state
  );
}

/** Unified-diff lines for display, with old/new line numbers. Text only. */
export function parseUnifiedDiff(diff) {
  const lines = (typeof diff === 'string' ? diff : '').split('\n');
  if (lines.at(-1) === '') lines.pop();
  let oldNo = 0;
  let newNo = 0;
  return lines.map((text) => {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(text);
    if (hunk) {
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[2]);
      return { kind: 'hunk', text, oldNo: null, newNo: null };
    }
    if (text.startsWith('+++ ') || text.startsWith('--- ') || text.startsWith('\\')) return { kind: 'meta', text, oldNo: null, newNo: null };
    if (text.startsWith('+')) return { kind: 'add', text, oldNo: null, newNo: newNo++ };
    if (text.startsWith('-')) return { kind: 'del', text, oldNo: oldNo++, newNo: null };
    return { kind: 'context', text, oldNo: oldNo++, newNo: newNo++ };
  });
}

export function diffStats(lines) {
  return {
    added: lines.filter((l) => l.kind === 'add').length,
    removed: lines.filter((l) => l.kind === 'del').length,
  };
}

/** `localHash` value when the browser has no Web Crypto (insecure context). */
export const HASH_UNAVAILABLE = 'unavailable';

/** SHA-256 hex of a string's UTF-8 bytes (Web Crypto; secure contexts only). */
export async function sha256Hex(text) {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return null;
  const digest = await subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Whether Approve/Reject may be enabled, and if not, why. `localHash` is
 * the hash the browser computed over the displayed diff (null while
 * computing or unavailable).
 */
export function approvalGate(task, { now = Date.now(), localHash = null, pending = false } = {}) {
  const proposal = task?.proposal;
  if (task?.status !== 'awaiting_approval' || !proposal) return { enabled: false, reason: 'This task is not awaiting approval.' };
  if (proposal.state !== 'pending' || !proposal.interrupt_id) return { enabled: false, reason: 'This proposal is no longer pending.' };
  const expiresAt = Date.parse(proposal.expires_at ?? '');
  if (!Number.isFinite(expiresAt) || expiresAt <= now) return { enabled: false, reason: 'The approval window has expired.' };
  if (localHash === null) return { enabled: false, reason: 'Verifying the displayed diff…' };
  if (localHash === HASH_UNAVAILABLE) {
    // Not a mismatch: Web Crypto exists only in secure contexts.
    return {
      enabled: false,
      reason: 'This browser cannot verify the diff here (Web Crypto needs HTTPS or localhost). Open the app over HTTPS or on 127.0.0.1 to decide.',
    };
  }
  if (localHash !== proposal.diff_sha256) {
    return { enabled: false, reason: 'The displayed diff does not match the server’s hash. Reload before deciding.' };
  }
  if (pending) return { enabled: false, reason: 'Sending your decision…' };
  return { enabled: true, reason: null };
}

/** Body for POST /api/agent-tasks/:id/decision — booleans only. */
export function decisionBody(task, approved, localHash) {
  if (typeof approved !== 'boolean') throw new TypeError('approved must be a boolean');
  return { approved, interruptId: task.proposal.interrupt_id, diffSha256: localHash };
}

export function formatRemaining(expiresAt, now = Date.now()) {
  const ms = Date.parse(expiresAt ?? '') - now;
  if (!Number.isFinite(ms) || ms <= 0) return 'expired';
  const minutes = Math.floor(ms / 60_000);
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}m left`;
  if (minutes >= 1) return `${minutes}m left`;
  return `${Math.ceil(ms / 1000)}s left`;
}

/** Exponential backoff after failed polls: 2s, 4s, 8s … capped at 15s. */
export function nextPollDelay(consecutiveFailures) {
  if (consecutiveFailures <= 0) return POLL_INTERVAL_MS;
  return Math.min(15_000, POLL_INTERVAL_MS * 2 ** consecutiveFailures);
}

const ERRORS = {
  ...SERVER_SIDE_MESSAGES,
  invalid_instruction: 'Enter an instruction (up to 2,000 characters).',
  invalid_decision: 'The decision was malformed and was not sent.',
  invalid_request: 'The request was rejected. Check the form and try again.',
  route_not_supported: 'Standalone test runs are not available yet (planned for F7). Describe a question or a code change instead.',
  agent_task_active: 'This repository already has an active task. Finish, reject, or wait for it before starting another.',
  agent_worker_unavailable: 'The backend’s agent worker is not running, so tasks and decisions cannot be processed.',
  agent_task_not_found: 'This task no longer exists.',
  invalid_task_id: 'That is not a valid task id.',
  approval_not_pending: 'This proposal is no longer awaiting a decision (it was already decided, or the task moved on).',
  approval_stale: 'This decision was for an earlier proposal. Review the current one.',
  approval_diff_mismatch: 'The diff changed since you loaded it. Reload and review it again.',
  approval_expired: 'The approval window expired. Nothing was written.',
  approval_invalid: 'The decision was refused as invalid.',
  approval_in_progress: 'Another decision for this task is being processed.',
  checkpoint_serialization_refused: 'The agent produced data that could not be stored safely, so the task was stopped.',
  repository_files_unavailable: 'The repository’s files are no longer on the server.',
  repository_not_found: 'Repository not found.',
  worker_interrupted: 'The backend stopped while this task was running.',
  database_unavailable: 'The database was unavailable.',
  schema_not_initialized: 'The database schema is not initialized (run backend/scripts/apply_schema.py).',
  llm_quota_exceeded: 'The free AI quota ran out during this task, so it stopped. Nothing further was retried.',
  llm_not_configured: 'The AI provider is not configured on the server.',
  internal_error: 'The task failed unexpectedly. Details are in the backend log.',
  unauthenticated: 'Your session has expired. Sign in again.',
  csrf_token_invalid: 'The request could not be verified. Reload the page and try again.',
  forbidden_origin: 'The request could not be verified. Reload the page and try again.',
  backend_unreachable: 'The backend is not reachable.',
  frontend_backend_not_configured: 'The frontend’s backend connection is not configured.',
};

export function agentTaskErrorMessage(code) {
  return ERRORS[code] ?? 'The request failed.';
}
