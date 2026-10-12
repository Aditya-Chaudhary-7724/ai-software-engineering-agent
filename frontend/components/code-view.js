import { ShieldAlert } from 'lucide-react';

import { formatBytes } from '@/lib/format';

/**
 * Repository content is UNTRUSTED DATA. It is rendered only as React text
 * (auto-escaped) — never as HTML or Markdown, never executed — and is
 * visibly labelled as untrusted, so a comment like "ignore previous
 * instructions" in a file is just text on screen.
 *
 * F4: each line has an anchor (`#L<n>`) and an optional `highlight` range
 * (from an Ask citation) is marked, so a citation lands on exact lines.
 */
export default function CodeView({ file, highlight = null }) {
  const lines = file.content.split('\n');
  if (lines.at(-1) === '') lines.pop();
  const inRange = (n) => highlight && n >= highlight.start && n <= highlight.end;

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-rule px-4 py-2">
        <span className="truncate font-mono text-[0.8125rem]">{file.relative_path}</span>
        <span className="flex items-center gap-3 font-mono text-[0.6875rem] text-ink-faint">
          {highlight ? <span className="text-accent">cited lines {highlight.start}–{highlight.end}</span> : null}
          <span>{file.language ?? 'plain text'}</span>
          <span>{lines.length} lines</span>
          <span>{formatBytes(file.size_bytes)}</span>
        </span>
      </div>
      <p className="flex items-center gap-1.5 border-b border-rule bg-sunken px-4 py-1 text-[0.6875rem] text-ink-muted">
        <ShieldAlert aria-hidden size={12} strokeWidth={1.75} />
        Repository content — untrusted data, displayed as text only.
      </p>
      {file.truncated ? (
        <p className="border-b border-rule bg-warn/5 px-4 py-1 text-xs text-warn">Showing the first 100,000 characters only.</p>
      ) : null}
      {highlight && highlight.start > lines.length ? (
        <p className="border-b border-rule bg-warn/5 px-4 py-1 text-xs text-warn">
          The cited lines are beyond the end of the current file — it may have changed since it was indexed. Re-index to refresh citations.
        </p>
      ) : null}
      <div className="overflow-x-auto py-2 font-mono text-[0.8125rem] leading-5">
        <table className="min-w-full border-collapse">
          <tbody>
            {lines.map((line, i) => {
              const n = i + 1;
              const marked = inRange(n);
              return (
                <tr key={n} id={`L${n}`} className={marked ? 'bg-accent-soft' : undefined}>
                  <td
                    aria-hidden
                    className={`w-px select-none whitespace-nowrap border-r px-3 text-right align-top ${marked ? 'border-accent text-accent' : 'border-rule text-ink-faint'}`}
                  >
                    {n}
                  </td>
                  <td className="whitespace-pre px-4 align-top">{line || ' '}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
