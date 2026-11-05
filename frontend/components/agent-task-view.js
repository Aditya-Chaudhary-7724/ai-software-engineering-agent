'use client';

import { Check, FileCode2, LoaderCircle, X } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';

import {
  HASH_UNAVAILABLE,
  MAX_POLL_FAILURES,
  agentTaskErrorMessage,
  approvalGate,
  changeSummary,
  decisionBody,
  formatRemaining,
  nextPollDelay,
  sha256Hex,
  shouldPoll,
  statusLabel,
  statusTone,
} from '@/lib/agent-tasks';
import { citationHref, foundViaLabel } from '@/lib/ask';
import { formatTimestamp } from '@/lib/format';
import { errorCodeFromResponse } from '@/lib/response-errors';

import DiffView from './diff-view';
import { Panel, StatusDot, Tag } from './ui';

/**
 * One agent task (F6). Polls `/api/agent-tasks/:id` (this app's own,
 * session-protected route) while the task is queued/running; stops when it
 * awaits approval or is terminal, on unmount, and after repeated failures.
 *
 * Approval: never automatic. Approve needs a second, explicit confirmation
 * click; both buttons are disabled unless `approvalGate` passes, which
 * includes the browser's own SHA-256 of the displayed diff matching the
 * server's. All agent output is rendered as plain React text.
 */
export default function AgentTaskView({ initialTask, csrfToken, repositoryName }) {
  const [task, setTask] = useState(initialTask);
  const [failures, setFailures] = useState(0);
  const [stoppedPolling, setStoppedPolling] = useState(null);
  const [localHash, setLocalHash] = useState(null);
  const [now, setNow] = useState(() => Date.now());
  const [confirming, setConfirming] = useState(false);
  const [decisionPending, setDecisionPending] = useState(false);
  const [decisionError, setDecisionError] = useState(null);

  const diff = task.proposal?.diff ?? null;

  useEffect(() => {
    if (!shouldPoll(task) || stoppedPolling) return undefined;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const response = await fetch(`/api/agent-tasks/${task.id}`, { cache: 'no-store', credentials: 'same-origin' });
        if (cancelled) return;
        if (response.status === 401) return setStoppedPolling('unauthenticated');
        if (response.status === 404) return setStoppedPolling('agent_task_not_found');
        if (!response.ok) throw new Error('poll failed');
        setTask(await response.json());
        setFailures(0);
      } catch {
        if (cancelled) return;
        const next = failures + 1;
        setFailures(next);
        if (next >= MAX_POLL_FAILURES) setStoppedPolling('backend_unreachable');
      }
    }, nextPollDelay(failures));
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [task, failures, stoppedPolling]);

  // Hash exactly the text being displayed.
  useEffect(() => {
    let cancelled = false;
    setLocalHash(null);
    if (diff !== null) sha256Hex(diff).then((hash) => !cancelled && setLocalHash(hash ?? HASH_UNAVAILABLE));
    return () => {
      cancelled = true;
    };
  }, [diff]);

  // Re-evaluate the approval window while a decision is possible.
  useEffect(() => {
    if (task.status !== 'awaiting_approval') return undefined;
    const timer = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(timer);
  }, [task.status]);

  const gate = approvalGate(task, { now, localHash, pending: decisionPending });

  async function decide(approved) {
    if (!gate.enabled) return;
    setDecisionPending(true);
    setDecisionError(null);
    try {
      const response = await fetch(`/api/agent-tasks/${task.id}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-csrf-token': csrfToken },
        body: JSON.stringify(decisionBody(task, approved, localHash)),
        credentials: 'same-origin',
      });
      const body = await response.json().catch(() => ({}));
      if (response.ok && typeof body.id === 'string') {
        setTask(body);
        setStoppedPolling(null);
      } else {
        setDecisionError(errorCodeFromResponse(response.status, body));
        // Refresh: the task may have moved on (expired, decided elsewhere).
        const latest = await fetch(`/api/agent-tasks/${task.id}`, { cache: 'no-store', credentials: 'same-origin' });
        if (latest.ok) setTask(await latest.json());
      }
    } catch {
      setDecisionError('backend_unreachable');
    }
    setConfirming(false);
    setDecisionPending(false);
  }

  const summary = changeSummary(task);
  const summaryStyle = {
    ok: 'border-ok bg-ok/5 text-ok',
    warn: 'border-warn bg-warn/5 text-warn',
    err: 'border-err bg-err/5 text-err',
    info: 'border-rule-strong text-ink',
  }[summary.tone];

  return (
    <div className="space-y-6">
      <div className="border border-rule bg-surface" aria-live="polite">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-rule px-4 py-2.5">
          <span className="flex items-center gap-3 text-sm">
            <StatusDot tone={statusTone(task.status)} label={statusLabel(task.status)} />
            {shouldPoll(task) && !stoppedPolling ? <LoaderCircle aria-hidden size={13} className="animate-spin text-accent motion-reduce:animate-none" /> : null}
          </span>
          <span className="flex items-center gap-3 text-xs">
            <Tag tone={task.route === 'modify' ? 'accent' : 'neutral'}>route: {task.route}</Tag>
            <span className="font-mono text-ink-faint">{task.id.slice(0, 8)}</span>
          </span>
        </div>
        <div className={`border-l-2 px-4 py-2 text-sm ${summaryStyle}`}>
          <span className="font-medium">{summary.title}.</span> <span className="text-ink-muted">{summary.text}</span>
        </div>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 border-t border-rule px-4 py-3 text-sm">
          <dt className="text-ink-muted">Instruction</dt>
          <dd className="whitespace-pre-wrap break-words">{task.instruction}</dd>
          <dt className="text-ink-muted">Repository</dt>
          <dd>
            <Link href={`/repositories/${task.repository_id}`} className="text-accent hover:underline">{repositoryName ?? `#${task.repository_id}`}</Link>
          </dd>
          <dt className="text-ink-muted">Generator</dt>
          <dd className="font-mono text-xs">
            {task.outcome.generator ?? '—'}
            {task.outcome.is_placeholder ? <span className="ml-2 text-warn">(stub LLM — proposed content is placeholder text, not real code)</span> : null}
          </dd>
          <dt className="text-ink-muted">Submitted</dt>
          <dd className="font-mono text-xs">{formatTimestamp(task.created_at)}</dd>
          {task.decided_at ? (
            <>
              <dt className="text-ink-muted">Last decision</dt>
              <dd className="font-mono text-xs">{formatTimestamp(task.decided_at)} · approvals granted: {task.approvals_granted}</dd>
            </>
          ) : null}
          {task.finished_at ? (
            <>
              <dt className="text-ink-muted">Finished</dt>
              <dd className="font-mono text-xs">{formatTimestamp(task.finished_at)}</dd>
            </>
          ) : null}
          {task.outcome.test_ran ? (
            <>
              <dt className="text-ink-muted">Sandbox tests</dt>
              <dd className={task.outcome.test_passed ? 'text-ok' : 'text-err'}>{task.outcome.test_passed ? 'passed' : 'failed'}</dd>
            </>
          ) : null}
        </dl>
        {stoppedPolling ? (
          <div className="flex items-center justify-between gap-3 border-t border-rule bg-warn/5 px-4 py-2 text-sm">
            <span className="text-warn">Stopped checking status: {agentTaskErrorMessage(stoppedPolling)}</span>
            {stoppedPolling === 'backend_unreachable' ? (
              <button type="button" className="text-accent hover:underline" onClick={() => { setFailures(0); setStoppedPolling(null); }}>Retry</button>
            ) : null}
          </div>
        ) : !shouldPoll(task) || failures === 0 ? null : (
          <p className="border-t border-rule px-4 py-2 text-xs text-warn">Lost contact with the server; retrying…</p>
        )}
      </div>

      {task.proposal ? (
        <section className="space-y-3">
          <h2 className="label-micro">Change review</h2>
          {task.proposal.message ? <p className="text-sm text-ink-muted">{task.proposal.message}</p> : null}
          <DiffView proposal={task.proposal} />
          {task.status === 'awaiting_approval' ? (
            <div className="space-y-2 border border-rule bg-surface p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm">
                  Approve writing this diff to <span className="font-mono">{task.proposal.relative_path}</span> on the server checkout?
                  <span className="ml-2 font-mono text-xs text-ink-faint">{formatRemaining(task.proposal.expires_at, now)}</span>
                </p>
                <span className="flex items-center gap-2">
                  {confirming ? (
                    <>
                      <button type="button" onClick={() => setConfirming(false)} disabled={decisionPending} className="border border-rule-strong px-3 py-1.5 text-sm hover:bg-sunken disabled:opacity-40">Cancel</button>
                      <button type="button" onClick={() => decide(true)} disabled={!gate.enabled} className="inline-flex items-center gap-1.5 bg-ok px-3 py-1.5 text-sm font-medium text-paper disabled:opacity-40">
                        <Check aria-hidden size={14} /> Confirm: apply this diff
                      </button>
                    </>
                  ) : (
                    <>
                      <button type="button" onClick={() => decide(false)} disabled={!gate.enabled} className="inline-flex items-center gap-1.5 border border-err/50 px-3 py-1.5 text-sm text-err hover:bg-err/5 disabled:opacity-40">
                        <X aria-hidden size={14} /> Reject
                      </button>
                      <button type="button" onClick={() => setConfirming(true)} disabled={!gate.enabled} className="inline-flex items-center gap-1.5 border border-ok/60 px-3 py-1.5 text-sm text-ok hover:bg-ok/5 disabled:opacity-40">
                        <Check aria-hidden size={14} /> Approve…
                      </button>
                    </>
                  )}
                </span>
              </div>
              {!gate.enabled && gate.reason ? <p className="text-xs text-warn">{gate.reason}</p> : null}
              <p className="text-xs text-ink-faint">
                Approval is bound to this exact diff (sha256 above) and can be used once. Approving writes one file on the server; it does not commit, push, or open a pull request. Sandboxed tests run afterwards if the repository has them; a failing run may propose a fix, which needs its own approval.
              </p>
            </div>
          ) : null}
          {decisionError ? (
            <p role="alert" className="border-l-2 border-err px-3 py-1.5 text-sm text-err">
              {agentTaskErrorMessage(decisionError)} <span className="font-mono text-xs text-ink-faint">({decisionError})</span>
            </p>
          ) : null}
        </section>
      ) : null}

      {task.final_response ? (
        <Panel title="Agent response" meta="untrusted output · plain text">
          <div className={`whitespace-pre-wrap break-words px-4 py-3 text-sm leading-6 ${task.outcome.is_placeholder ? 'font-mono text-ink-muted' : ''}`}>{task.final_response}</div>
        </Panel>
      ) : null}

      {task.sources.length ? (
        <Panel title="Sources" meta={`${task.sources.length}`}>
          <ol className="py-2">
            {task.sources.map((source, i) => (
              <li key={`${source.relative_path}:${source.start_line ?? i}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-1.5">
                <FileCode2 aria-hidden size={13} className="text-ink-faint" />
                {source.start_line ? (
                  <Link href={citationHref(task.repository_id, source)} className="font-mono text-[0.8125rem] text-accent hover:underline">
                    {source.relative_path}:{source.start_line}–{source.end_line}
                  </Link>
                ) : (
                  <span className="font-mono text-[0.8125rem]">{source.relative_path}</span>
                )}
                {source.found_via.map((via) => <Tag key={via}>{foundViaLabel(via)}</Tag>)}
              </li>
            ))}
          </ol>
        </Panel>
      ) : null}

      <Panel title="Execution log" meta="safe step summaries · no model reasoning">
        {task.execution_log.length === 0 ? (
          <p className="px-4 py-3 text-sm text-ink-muted">{task.status === 'queued' ? 'Waiting for the agent worker…' : 'No steps recorded yet.'}</p>
        ) : (
          <ol className="py-2 font-mono text-[0.8125rem]">
            {task.execution_log.map((entry, i) => (
              <li key={i} className="flex gap-3 px-4 py-1">
                <span className="w-6 shrink-0 text-right text-ink-faint">{i + 1}</span>
                <span className="whitespace-pre-wrap break-words">{entry}</span>
              </li>
            ))}
          </ol>
        )}
        <div className="border-t border-rule px-4 py-2 text-right text-xs">
          <Link href={`/traces/${task.trace_id}`} className="text-accent hover:underline">Open trace →</Link>
        </div>
      </Panel>
    </div>
  );
}
