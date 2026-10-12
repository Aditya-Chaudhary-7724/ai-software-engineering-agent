'use client';

import Link from 'next/link';
import { useState } from 'react';

import { isTerminal, jobErrorMessage, validateGithubUrl } from '@/lib/jobs';
import { errorCodeFromResponse } from '@/lib/response-errors';

import JobProgress from './job-progress';

/**
 * Submits a GitHub HTTPS URL. The browser sends only the URL and a graph
 * flag — never a path; the server decides where the clone goes. Submit is
 * disabled while a request is in flight and while the job for the same
 * repository is still running (the backend also de-duplicates).
 */
export default function AddRepositoryForm({ csrfToken, graphAvailable }) {
  const [url, setUrl] = useState('');
  const [buildGraph, setBuildGraph] = useState(false);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState(null);
  const [existingRepositoryId, setExistingRepositoryId] = useState(null);
  const [jobs, setJobs] = useState([]); // [{ fullName, job, created }]

  const check = validateGithubUrl(url);
  const activeForThisUrl = check.ok && jobs.some((j) => j.fullName === check.fullName && !isTerminal(j.job) && !j.finished);
  const disabled = pending || !check.ok || activeForThisUrl;

  async function onSubmit(event) {
    event.preventDefault();
    if (disabled) return;
    setPending(true);
    setMessage(null);
    setExistingRepositoryId(null);
    try {
      const response = await fetch('/api/ingestion-jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-csrf-token': csrfToken },
        body: JSON.stringify({ githubUrl: url.trim(), buildGraph: graphAvailable && buildGraph }),
        credentials: 'same-origin',
      });
      const body = await response.json().catch(() => ({}));
      if (response.ok && body.job) {
        setJobs((current) => [{ fullName: check.fullName, job: body.job, created: body.created }, ...current.filter((j) => j.job.id !== body.job.id)]);
        if (!body.created) setMessage({ tone: 'info', text: 'This repository already has a job in progress; showing it below.' });
        else setUrl('');
      } else {
        if (Number.isInteger(body.repositoryId)) setExistingRepositoryId(body.repositoryId);
        const code = errorCodeFromResponse(response.status, body);
        setMessage({ tone: 'error', text: jobErrorMessage(code), code });
      }
    } catch {
      setMessage({ tone: 'error', text: jobErrorMessage('backend_unreachable'), code: 'backend_unreachable' });
    }
    setPending(false);
  }

  const markFinished = (id) => setJobs((current) => current.map((j) => (j.job.id === id ? { ...j, finished: true } : j)));

  return (
    <div className="space-y-4">
      <form onSubmit={onSubmit} noValidate className="grid gap-3 p-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-start">
        <div>
          <label htmlFor="github-url" className="label-micro mb-1.5 block">GitHub repository URL</label>
          <input
            id="github-url"
            type="url"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            maxLength={300}
            placeholder="https://github.com/owner/repository"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            aria-invalid={url && !check.ok ? 'true' : undefined}
            aria-describedby="github-url-hint"
            className="block w-full border border-rule-strong bg-surface px-3 py-2 font-mono text-sm outline-none focus:border-accent"
          />
          <p id="github-url-hint" className={`mt-1.5 text-xs ${url && !check.ok ? 'text-err' : 'text-ink-faint'}`}>
            {url && !check.ok ? check.error : 'Public repositories work without a token. Only https://github.com/<owner>/<repo> is accepted.'}
          </p>
          <label className={`mt-3 flex items-center gap-2 text-sm ${graphAvailable ? '' : 'text-ink-faint'}`}>
            <input type="checkbox" checked={graphAvailable && buildGraph} disabled={!graphAvailable} onChange={(e) => setBuildGraph(e.target.checked)} />
            Also build the knowledge graph
            {!graphAvailable ? <span className="text-xs">(Neo4j not available)</span> : null}
          </label>
        </div>
        <button
          type="submit"
          disabled={disabled}
          className="bg-ink px-4 py-2 text-sm font-medium text-paper transition-opacity disabled:opacity-40 md:mt-6"
        >
          {pending ? 'Submitting…' : activeForThisUrl ? 'In progress' : 'Add repository'}
        </button>
      </form>

      {message ? (
        <p role={message.tone === 'error' ? 'alert' : 'status'} className={`mx-4 border-l-2 px-3 py-1.5 text-sm ${message.tone === 'error' ? 'border-err text-err' : 'border-accent text-ink-muted'}`}>
          {message.text}
          {existingRepositoryId ? (
            <Link href={`/repositories/${existingRepositoryId}`} className="ml-2 text-accent hover:underline">Open it →</Link>
          ) : null}
          {message.code ? <span className="ml-2 font-mono text-xs text-ink-faint">({message.code})</span> : null}
        </p>
      ) : null}

      {jobs.length ? (
        <div className="space-y-3 px-4 pb-4">
          {jobs.map(({ job }) => (
            <JobProgress key={job.id} initialJob={job} onTerminal={() => markFinished(job.id)} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
