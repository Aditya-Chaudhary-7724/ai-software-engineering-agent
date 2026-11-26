import { FileCode2, Sparkles } from 'lucide-react';
import Link from 'next/link';

import { citationHref } from '@/lib/ask';
import { splitExamples } from '@/lib/demo';
import { formatTimestamp } from '@/lib/format';

import DiffView from './diff-view';
import { ErrorNotice, Panel, Tag } from './ui';

/**
 * Pre-generated demo examples for one repository: served from the database,
 * no LLM call, no quota. Model output from untrusted repository content, so
 * it is rendered as plain React text; only citations (retrieval metadata)
 * become links. `show` picks the Q&As, the example task diff, or both.
 */
function ExampleMeta({ example }) {
  return (
    <span className="flex flex-wrap items-center gap-2 text-[0.6875rem] text-ink-faint">
      <Tag tone="accent">pre-generated example</Tag>
      <span className="font-mono">{example.generator}</span>
      {example.generated_at ? <span className="font-mono">{formatTimestamp(example.generated_at)}</span> : null}
    </span>
  );
}

export default function DemoExamples({ repository, result, show = 'all' }) {
  const name = repository.github_full_name ?? repository.name;
  if (!result.ok) {
    return (
      <Panel title={`Examples · ${name}`} bodyClassName="p-4">
        <ErrorNotice code={result.error} compact />
      </Panel>
    );
  }
  const { asks, task } = splitExamples(result.data.examples);
  const wantAsks = show !== 'task' && asks.length > 0;
  const wantTask = show !== 'ask' && task !== null;
  if (!wantAsks && !wantTask) return null;

  return (
    <Panel title={`Examples · ${name}`} meta="instant · uses no demo quota">
      {wantAsks ? (
        <ul className="divide-y divide-rule">
          {asks.map((example) => (
            <li key={`ask-${example.prompt}`}>
              <details className="group px-4 py-3">
                <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2 text-sm font-medium">
                  <span className="flex items-center gap-2">
                    <Sparkles aria-hidden size={13} className="text-accent" />
                    {example.prompt}
                  </span>
                  <ExampleMeta example={example} />
                </summary>
                <div className="mt-3 space-y-3">
                  <div className="whitespace-pre-wrap break-words text-sm leading-6">{example.answer}</div>
                  {example.sources.length ? (
                    <ol className="space-y-1">
                      {example.sources.map((source, i) => (
                        <li key={`${source.relative_path}:${source.start_line ?? i}`} className="flex items-center gap-2">
                          <FileCode2 aria-hidden size={13} className="text-ink-faint" />
                          {source.start_line ? (
                            <Link href={citationHref(repository.id, source)} className="font-mono text-[0.8125rem] text-accent hover:underline">
                              {source.relative_path}:{source.start_line}–{source.end_line}
                            </Link>
                          ) : (
                            <span className="font-mono text-[0.8125rem]">{source.relative_path}</span>
                          )}
                        </li>
                      ))}
                    </ol>
                  ) : null}
                </div>
              </details>
            </li>
          ))}
        </ul>
      ) : null}
      {wantTask ? (
        <div className={`space-y-3 px-4 py-3 ${wantAsks ? 'border-t border-rule' : ''}`}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm"><span className="font-medium">Example task:</span> {task.prompt}</p>
            <ExampleMeta example={task} />
          </div>
          <DiffView proposal={{ relative_path: task.diff_relative_path, diff: task.diff, state: 'display_only' }} />
        </div>
      ) : null}
    </Panel>
  );
}
