'use client';

import { FileCode2, MessageSquareText } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';

import { answerNotices, askErrorMessage, citationHref, foundViaLabel } from '@/lib/ask';
import { errorCodeFromResponse } from '@/lib/response-errors';

import { Tag } from './ui';

const MAX_QUESTION_CHARS = 2000;

/**
 * Ask (F4): strictly read-only Q&A. The answer is generated from UNTRUSTED
 * repository content, so it is shown as plain text (no Markdown/HTML, no
 * auto-linked URLs); only the citations — which come from retrieval
 * metadata, not from the model's text — are rendered as links.
 */
export default function AskPanel({ repositories, initialRepositoryId, csrfToken }) {
  const [repositoryId, setRepositoryId] = useState(String(initialRepositoryId ?? repositories[0]?.id ?? ''));
  const [question, setQuestion] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const [history, setHistory] = useState([]); // newest first, this page only — never persisted

  const trimmed = question.trim();
  const canSubmit = !pending && repositoryId && trimmed.length > 0 && trimmed.length <= MAX_QUESTION_CHARS;

  async function onSubmit(event) {
    event.preventDefault();
    if (!canSubmit) return;
    setPending(true);
    setError(null);
    try {
      const response = await fetch(`/api/repositories/${repositoryId}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-csrf-token': csrfToken },
        body: JSON.stringify({ question: trimmed }),
        credentials: 'same-origin',
      });
      const body = await response.json().catch(() => ({}));
      if (response.ok && body.answer) {
        const repository = repositories.find((r) => String(r.id) === repositoryId);
        setHistory((current) => [{ response: body, repositoryName: repository?.github_full_name ?? repository?.name }, ...current]);
        setQuestion('');
      } else {
        setError(errorCodeFromResponse(response.status, body));
      }
    } catch {
      setError('backend_unreachable');
    }
    setPending(false);
  }

  return (
    <div className="space-y-6">
      <form onSubmit={onSubmit} className="space-y-3 border border-rule bg-surface p-4">
        <div className="grid gap-3 md:grid-cols-[16rem_minmax(0,1fr)]">
          <div>
            <label htmlFor="ask-repository" className="label-micro mb-1.5 block">Repository</label>
            <select
              id="ask-repository"
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
            <label htmlFor="ask-question" className="label-micro mb-1.5 block">Question</label>
            <textarea
              id="ask-question"
              rows={3}
              maxLength={MAX_QUESTION_CHARS}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) onSubmit(event);
              }}
              placeholder="Where is the HTML escaping implemented?"
              className="block w-full resize-y border border-rule-strong bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
            />
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-ink-faint">
            Read-only: Ask retrieves indexed code and generates text. It never edits files, runs tests, or touches GitHub. ⌘/Ctrl + Enter to send.
          </p>
          <span className="flex items-center gap-3">
            <span className="font-mono text-[0.6875rem] text-ink-faint">{trimmed.length}/{MAX_QUESTION_CHARS}</span>
            <button type="submit" disabled={!canSubmit} className="bg-ink px-4 py-2 text-sm font-medium text-paper transition-opacity disabled:opacity-40">
              {pending ? 'Retrieving & answering…' : 'Ask'}
            </button>
          </span>
        </div>
        {error ? (
          <p role="alert" className="border-l-2 border-err px-3 py-1.5 text-sm text-err">
            {askErrorMessage(error)} <span className="font-mono text-xs text-ink-faint">({error})</span>
          </p>
        ) : null}
      </form>

      {history.length === 0 ? (
        <p className="text-sm text-ink-muted">Answers appear here with the exact code they were built from.</p>
      ) : (
        <ol className="space-y-6" aria-live="polite">
          {history.map(({ response, repositoryName }, index) => (
            <li key={history.length - index} className="border border-rule bg-surface">
              <AnswerCard response={response} repositoryName={repositoryName} />
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function AnswerCard({ response, repositoryName }) {
  const notices = answerNotices(response);
  return (
    <article>
      <header className="flex flex-wrap items-baseline justify-between gap-3 border-b border-rule px-4 py-3">
        <p className="flex min-w-0 items-start gap-2 text-sm font-medium">
          <MessageSquareText aria-hidden size={15} className="mt-0.5 shrink-0 text-ink-faint" />
          <span className="whitespace-pre-wrap break-words">{response.question}</span>
        </p>
        <span className="font-mono text-[0.6875rem] text-ink-faint">{repositoryName}</span>
      </header>

      {notices.length ? (
        <ul className="border-b border-rule">
          {notices.map((n) => (
            <li key={n.title} className={`border-l-2 px-4 py-1.5 text-xs ${n.tone === 'warn' ? 'border-warn bg-warn/5' : 'border-rule-strong'}`}>
              <span className={`font-medium ${n.tone === 'warn' ? 'text-warn' : 'text-ink'}`}>{n.title}.</span>{' '}
              <span className="text-ink-muted">{n.text}</span>
            </li>
          ))}
        </ul>
      ) : null}

      <section className="px-4 py-3">
        <p className="label-micro mb-1.5">
          {response.answer.is_placeholder ? 'Stub output' : `Generated answer · ${response.answer.generator}`} — from untrusted repository content; verify against the sources
        </p>
        <div className={`whitespace-pre-wrap break-words text-sm leading-6 ${response.answer.is_placeholder ? 'font-mono text-ink-muted' : ''}`}>
          {response.answer.text}
        </div>
      </section>

      <section className="border-t border-rule">
        <p className="label-micro px-4 pb-1.5 pt-3">
          Sources · {response.sources.length} chunk{response.sources.length === 1 ? '' : 's'} given to the model
        </p>
        {response.sources.length === 0 ? (
          <p className="px-4 pb-3 text-sm text-ink-muted">None — no indexed code matched this question.</p>
        ) : (
          <ol className="pb-2">
            {response.sources.map((source, i) => (
              <li key={`${source.relative_path}:${source.start_line}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-1.5 hover:bg-sunken">
                <span className="w-5 font-mono text-[0.6875rem] text-ink-faint">[{i + 1}]</span>
                <Link href={citationHref(response.repository_id, source)} className="inline-flex min-w-0 items-center gap-1.5 font-mono text-[0.8125rem] text-accent hover:underline">
                  <FileCode2 aria-hidden size={13} className="shrink-0" />
                  <span className="truncate">{source.relative_path}</span>
                  <span className="text-ink-faint">:{source.start_line}–{source.end_line}</span>
                </Link>
                {source.symbol_name ? <span className="font-mono text-xs">{source.symbol_name}</span> : null}
                <Tag>{source.chunk_type}</Tag>
                {source.found_via.map((via) => <Tag key={via} tone={via.startsWith('graph') ? 'accent' : 'neutral'}>{foundViaLabel(via)}</Tag>)}
              </li>
            ))}
          </ol>
        )}
      </section>
    </article>
  );
}
