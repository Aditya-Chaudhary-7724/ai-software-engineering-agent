'use client';

import { RefreshCw } from 'lucide-react';
import { useState } from 'react';

import { isTerminal, jobErrorMessage } from '@/lib/jobs';
import { errorCodeFromResponse } from '@/lib/response-errors';

import JobProgress from './job-progress';

/**
 * Re-index an existing repository from its server-side checkout. Disabled
 * while that repository has a queued/running job (shown instead).
 */
export default function ReindexControl({ repositoryId, csrfToken, graphAvailable, activeJob, filesAvailable }) {
  const [job, setJob] = useState(activeJob ?? null);
  const [buildGraph, setBuildGraph] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const running = job && !isTerminal(job);

  async function reindex() {
    if (pending || running) return;
    setPending(true);
    setError(null);
    try {
      const response = await fetch(`/api/repositories/${repositoryId}/reindex`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-csrf-token': csrfToken },
        body: JSON.stringify({ buildGraph: graphAvailable && buildGraph }),
        credentials: 'same-origin',
      });
      const body = await response.json().catch(() => ({}));
      if (response.ok && body.job) setJob(body.job);
      else setError(errorCodeFromResponse(response.status, body));
    } catch {
      setError('backend_unreachable');
    }
    setPending(false);
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-ink-muted">
        Rebuilds this repository’s index from its checkout on the server. The previous index stays in place until the new one is complete.
      </p>
      <label className={`flex items-center gap-2 text-sm ${graphAvailable ? '' : 'text-ink-faint'}`}>
        <input type="checkbox" checked={graphAvailable && buildGraph} disabled={!graphAvailable || running} onChange={(e) => setBuildGraph(e.target.checked)} />
        Rebuild the knowledge graph
        {!graphAvailable ? <span className="text-xs">(Neo4j not available)</span> : null}
      </label>
      <button
        type="button"
        onClick={reindex}
        disabled={pending || running || !filesAvailable}
        className="inline-flex items-center gap-2 border border-rule-strong px-3 py-1.5 text-sm transition-colors hover:bg-sunken disabled:opacity-40"
      >
        <RefreshCw aria-hidden size={14} strokeWidth={1.75} />
        {pending ? 'Submitting…' : running ? 'Re-index in progress' : 'Re-index'}
      </button>
      {!filesAvailable ? <p className="text-xs text-warn">Unavailable: this repository’s files are no longer on the server.</p> : null}
      {error ? (
        <p role="alert" className="border-l-2 border-err px-3 py-1.5 text-sm text-err">
          {jobErrorMessage(error)} <span className="font-mono text-xs text-ink-faint">({error})</span>
        </p>
      ) : null}
      {job ? <JobProgress key={job.id} initialJob={job} /> : null}
    </div>
  );
}
