'use client';

import { Check, CircleDashed, LoaderCircle, X } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { formatCount, formatTimestamp } from '@/lib/format';
import { MAX_POLL_FAILURES, graphOutcome, isTerminal, jobErrorMessage, nextPollDelay, stageStates } from '@/lib/jobs';

import { StatusDot } from './ui';

/**
 * Shows one ingestion/re-index job and polls `/api/ingestion-jobs/:id`
 * (this app's own route, session-protected) until the job is terminal.
 * Polling stops on completion/failure, on unmount, and after repeated
 * network failures (then offers a manual retry). When a job finishes,
 * the server-rendered page is refreshed so the repository data updates.
 * All text from the backend is rendered as plain React text.
 */

const STATE_ICON = {
  done: <Check aria-hidden size={13} className="text-ok" />,
  current: <LoaderCircle aria-hidden size={13} className="animate-spin text-accent motion-reduce:animate-none" />,
  failed: <X aria-hidden size={13} className="text-err" />,
  pending: <CircleDashed aria-hidden size={13} className="text-ink-faint" />,
};

export default function JobProgress({ initialJob, onTerminal }) {
  const router = useRouter();
  const [job, setJob] = useState(initialJob);
  const [failures, setFailures] = useState(0);
  const [stoppedPolling, setStoppedPolling] = useState(false);
  const [retryToken, setRetryToken] = useState(0);
  const notifiedRef = useRef(isTerminal(initialJob));

  useEffect(() => {
    if (isTerminal(job) || stoppedPolling) return undefined;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const response = await fetch(`/api/ingestion-jobs/${job.id}`, { cache: 'no-store', credentials: 'same-origin' });
        if (cancelled) return;
        if (response.status === 401) return setStoppedPolling('unauthenticated');
        if (response.status === 404) return setStoppedPolling('job_not_found');
        if (!response.ok) throw new Error('poll failed');
        setJob(await response.json());
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
  }, [job, failures, stoppedPolling, retryToken]);

  useEffect(() => {
    if (isTerminal(job) && !notifiedRef.current) {
      notifiedRef.current = true;
      onTerminal?.(job);
      router.refresh();
    }
  }, [job, onTerminal, router]);

  const title = job.job_type === 'reindex' ? 'Re-index' : `Ingest ${job.github_full_name ?? 'repository'}`;

  return (
    <div className="border border-rule bg-surface" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-rule px-4 py-2.5">
        <span className="text-sm font-medium">{title}</span>
        <span className="flex items-center gap-3 text-xs">
          <StatusDot tone={job.status === 'queued' ? 'idle' : job.status} label={job.status} />
          <span className="font-mono text-ink-faint">{job.id.slice(0, 8)}</span>
        </span>
      </div>

      <ol className="grid gap-1 px-4 py-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
        {stageStates(job).map(({ stage, label, state }) => (
          <li key={stage} className={`flex items-center gap-2 ${state === 'pending' ? 'text-ink-faint' : ''}`}>
            {STATE_ICON[state]}
            <span>{label}</span>
          </li>
        ))}
      </ol>

      <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 border-t border-rule px-4 py-3 text-sm">
        <dt className="text-ink-muted">Knowledge graph</dt>
        <dd>
          {graphOutcome(job)}
          {job.progress?.graph_error_code ? <span className="ml-2 font-mono text-xs text-ink-faint">({job.progress.graph_error_code})</span> : null}
        </dd>
        {job.status === 'completed' ? (
          <>
            <dt className="text-ink-muted">Chunks indexed</dt>
            <dd className="font-mono text-xs">
              {formatCount(job.progress?.chunks_indexed)}
              {job.progress?.embedding_provider === 'deterministic_local' ? <span className="ml-2 text-ink-faint">(local, non-semantic embeddings)</span> : null}
            </dd>
          </>
        ) : null}
        <dt className="text-ink-muted">Submitted</dt>
        <dd className="font-mono text-xs">{formatTimestamp(job.created_at)}</dd>
        {job.finished_at ? (
          <>
            <dt className="text-ink-muted">Finished</dt>
            <dd className="font-mono text-xs">{formatTimestamp(job.finished_at)}</dd>
          </>
        ) : null}
      </dl>

      {job.status === 'failed' ? (
        <p role="alert" className="border-t border-rule bg-err/5 px-4 py-2 text-sm text-err">
          {jobErrorMessage(job.error_code)} <span className="font-mono text-xs text-ink-faint">({job.error_code})</span>
        </p>
      ) : null}

      {!isTerminal(job) && failures > 0 && !stoppedPolling ? (
        <p className="border-t border-rule px-4 py-2 text-xs text-warn">Lost contact with the server; retrying…</p>
      ) : null}

      {stoppedPolling ? (
        <div className="flex items-center justify-between gap-3 border-t border-rule bg-warn/5 px-4 py-2 text-sm">
          <span className="text-warn">Stopped checking status: {jobErrorMessage(stoppedPolling)}</span>
          {stoppedPolling === 'backend_unreachable' ? (
            <button
              type="button"
              className="text-accent hover:underline"
              onClick={() => {
                setFailures(0);
                setStoppedPolling(false);
                setRetryToken((n) => n + 1);
              }}
            >
              Retry
            </button>
          ) : null}
        </div>
      ) : null}

      {job.status === 'completed' && job.repository_id ? (
        <div className="border-t border-rule px-4 py-2 text-right text-sm">
          <Link href={`/repositories/${job.repository_id}`} className="text-accent hover:underline">Open repository →</Link>
        </div>
      ) : null}
    </div>
  );
}
