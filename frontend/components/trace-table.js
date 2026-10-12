import Link from 'next/link';

import { formatDuration, formatTimestamp } from '@/lib/format';

import { StatusDot } from './ui';

export default function TraceTable({ traces }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-rule text-left">
          {['Status', 'Request', 'Repo', 'Spans', 'Duration', 'Started'].map((h) => (
            <th key={h} scope="col" className="label-micro px-4 py-2 font-medium">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {traces.map((t) => (
          <tr key={t.trace_id} className="border-b border-rule last:border-0 hover:bg-sunken">
            <td className="whitespace-nowrap px-4 py-2 text-xs">
              <StatusDot tone={t.status} label={t.status.replace('_', ' ')} />
            </td>
            <td className="max-w-md px-4 py-2">
              <Link href={`/traces/${t.trace_id}`} className="block truncate hover:text-accent">
                {t.question || <span className="text-ink-faint">(no request recorded)</span>}
              </Link>
              <span className="font-mono text-[0.6875rem] text-ink-faint">{t.trace_id}</span>
            </td>
            <td className="px-4 py-2 font-mono text-xs text-ink-muted">{t.repository_id ?? '—'}</td>
            <td className="px-4 py-2 font-mono text-xs">
              {t.span_count}
              {t.error_span_count ? <span className="text-err"> · {t.error_span_count} err</span> : null}
            </td>
            <td className="px-4 py-2 font-mono text-xs">{formatDuration(t.duration_ms)}</td>
            <td className="whitespace-nowrap px-4 py-2 font-mono text-xs text-ink-muted">{formatTimestamp(t.started_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
