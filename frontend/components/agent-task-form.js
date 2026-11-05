'use client';

import { Route } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { MAX_INSTRUCTION_CHARS, ROUTE_TEXT, agentTaskErrorMessage } from '@/lib/agent-tasks';
import { errorCodeFromResponse } from '@/lib/response-errors';

import { Tag } from './ui';

/**
 * F6 task form. Two explicit steps, so the operator sees what the agent
 * will DO before it runs: (1) "Check route" asks the backend's own
 * deterministic classifier (the one the agent graph uses); (2) "Run task"
 * is enabled only for that exact, previewed text and a supported route.
 * Submitting only enqueues the task — even a "modify" task writes nothing
 * until you approve a specific diff on the task page.
 */
export default function AgentTaskForm({ repositories, csrfToken, initialRepositoryId }) {
  const router = useRouter();
  const [repositoryId, setRepositoryId] = useState(String(initialRepositoryId ?? repositories[0]?.id ?? ''));
  const [instruction, setInstruction] = useState('');
  const [preview, setPreview] = useState(null); // { text, route, supported, requires_approval }
  const [pending, setPending] = useState(null); // 'preview' | 'submit'
  const [error, setError] = useState(null);

  const trimmed = instruction.trim();
  const validLength = trimmed.length > 0 && trimmed.length <= MAX_INSTRUCTION_CHARS;
  const previewCurrent = preview && preview.text === trimmed;
  const canSubmit = !pending && repositoryId && validLength && previewCurrent && preview.supported;

  async function post(url, body) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-csrf-token': csrfToken },
      body: JSON.stringify(body),
      credentials: 'same-origin',
    });
    return { response, body: await response.json().catch(() => ({})) };
  }

  async function checkRoute() {
    if (pending || !validLength) return;
    setPending('preview');
    setError(null);
    try {
      const { response, body } = await post('/api/agent-tasks/route-preview', { instruction: trimmed });
      if (response.ok && typeof body.route === 'string') setPreview({ ...body, text: trimmed });
      else setError(errorCodeFromResponse(response.status, body));
    } catch {
      setError('backend_unreachable');
    }
    setPending(null);
  }

  async function submit(event) {
    event.preventDefault();
    if (!canSubmit) return;
    setPending('submit');
    setError(null);
    try {
      const { response, body } = await post(`/api/repositories/${repositoryId}/agent-tasks`, { instruction: trimmed });
      if (response.ok && typeof body.id === 'string') {
        router.push(`/tasks/${body.id}`);
        return;
      }
      setError(errorCodeFromResponse(response.status, body));
    } catch {
      setError('backend_unreachable');
    }
    setPending(null);
  }

  return (
    <form onSubmit={submit} className="space-y-3 border border-rule bg-surface p-4">
      <div className="grid gap-3 md:grid-cols-[16rem_minmax(0,1fr)]">
        <div>
          <label htmlFor="task-repository" className="label-micro mb-1.5 block">Repository</label>
          <select
            id="task-repository"
            value={repositoryId}
            onChange={(event) => setRepositoryId(event.target.value)}
            className="block w-full border border-rule-strong bg-surface px-2 py-2 text-sm outline-none focus:border-accent"
          >
            {repositories.map((r) => (
              <option key={r.id} value={String(r.id)}>{r.github_full_name ?? r.name} (#{r.id})</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="task-instruction" className="label-micro mb-1.5 block">Instruction</label>
          <textarea
            id="task-instruction"
            rows={3}
            maxLength={MAX_INSTRUCTION_CHARS}
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            placeholder="Fix the off-by-one error in the pagination helper"
            className="block w-full resize-y border border-rule-strong bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
          />
        </div>
      </div>

      {previewCurrent ? (
        <div className={`flex flex-wrap items-start gap-3 border-l-2 px-3 py-2 text-sm ${preview.supported ? 'border-accent bg-accent-soft/40' : 'border-warn bg-warn/5'}`}>
          <Route aria-hidden size={15} className="mt-0.5 shrink-0 text-ink-faint" />
          <span className="flex-1">
            <span className="font-medium">Detected route:</span> <Tag tone={preview.supported ? 'accent' : 'warn'}>{preview.route}</Tag>{' '}
            <span className="text-ink-muted">{ROUTE_TEXT[preview.route] ?? ''}</span>
          </span>
        </div>
      ) : preview ? (
        <p className="text-xs text-ink-faint">The instruction changed — check the route again before running.</p>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-xs text-ink-faint">
          Runs in the background. A change is only ever proposed as a diff; it is written to the server checkout only after you approve that exact diff. Nothing is committed, pushed, or sent to GitHub.
        </p>
        <span className="flex items-center gap-3">
          <span className="font-mono text-[0.6875rem] text-ink-faint">{trimmed.length}/{MAX_INSTRUCTION_CHARS}</span>
          <button
            type="button"
            onClick={checkRoute}
            disabled={Boolean(pending) || !validLength || previewCurrent}
            className="border border-rule-strong px-3 py-2 text-sm transition-colors hover:bg-sunken disabled:opacity-40"
          >
            {pending === 'preview' ? 'Checking…' : 'Check route'}
          </button>
          <button type="submit" disabled={!canSubmit} className="bg-ink px-4 py-2 text-sm font-medium text-paper transition-opacity disabled:opacity-40">
            {pending === 'submit' ? 'Submitting…' : 'Run task'}
          </button>
        </span>
      </div>

      {error ? (
        <p role="alert" className="border-l-2 border-err px-3 py-1.5 text-sm text-err">
          {agentTaskErrorMessage(error)} <span className="font-mono text-xs text-ink-faint">({error})</span>
        </p>
      ) : null}
    </form>
  );
}
