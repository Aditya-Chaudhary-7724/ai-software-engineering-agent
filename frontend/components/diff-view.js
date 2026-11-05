import { ShieldAlert } from 'lucide-react';

import { diffStats, parseUnifiedDiff, proposalStateLabel } from '@/lib/agent-tasks';

/**
 * A unified diff produced by the agent from UNTRUSTED repository content and
 * LLM output. Rendered strictly as React text (auto-escaped; no HTML,
 * Markdown, or links), with the proposal's state stated above it so a
 * proposed change can never be mistaken for an applied one.
 */
const ROW = {
  add: 'bg-ok/10',
  del: 'bg-err/10',
  hunk: 'bg-accent-soft text-accent',
  meta: 'text-ink-faint',
  context: '',
};

export default function DiffView({ proposal }) {
  const lines = parseUnifiedDiff(proposal.diff);
  const { added, removed } = diffStats(lines);
  const pending = proposal.state === 'pending';

  return (
    <div className="border border-rule bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-rule px-4 py-2">
        <span className="truncate font-mono text-[0.8125rem]">{proposal.relative_path ?? 'unknown file'}</span>
        <span className="flex items-center gap-3 font-mono text-[0.6875rem]">
          <span className="text-ok">+{added}</span>
          <span className="text-err">−{removed}</span>
          <span className={pending ? 'font-semibold text-warn' : 'text-ink-muted'}>{proposalStateLabel(proposal.state)}</span>
        </span>
      </div>
      <p className="flex items-center gap-1.5 border-b border-rule bg-sunken px-4 py-1 text-[0.6875rem] text-ink-muted">
        <ShieldAlert aria-hidden size={12} strokeWidth={1.75} />
        Generated from untrusted repository content — displayed as text only.
        {proposal.fix_iteration ? ` Fix attempt ${proposal.fix_iteration}.` : ''}
      </p>
      {lines.length === 0 ? (
        <p className="px-4 py-3 text-sm text-ink-muted">The proposal is an empty diff (no content change).</p>
      ) : (
        <div className="max-h-[32rem] overflow-auto py-1 font-mono text-[0.8125rem] leading-5">
          <table className="min-w-full border-collapse">
            <tbody>
              {lines.map((line, i) => (
                <tr key={i} className={ROW[line.kind]}>
                  <td aria-hidden className="w-px select-none whitespace-nowrap border-r border-rule px-2 text-right text-ink-faint">{line.oldNo ?? ''}</td>
                  <td aria-hidden className="w-px select-none whitespace-nowrap border-r border-rule px-2 text-right text-ink-faint">{line.newNo ?? ''}</td>
                  <td className="whitespace-pre px-3">{line.text || ' '}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="border-t border-rule px-4 py-1.5 font-mono text-[0.6875rem] text-ink-faint">sha256 {proposal.diff_sha256}</p>
    </div>
  );
}
