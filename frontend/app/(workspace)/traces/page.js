import Link from 'next/link';

import TraceTable from '@/components/trace-table';
import { EmptyState, ErrorNotice, PageHeader, Panel } from '@/components/ui';
import { formatCount } from '@/lib/format';
import { api } from '@/lib/server/backend';

export const metadata = { title: 'Agent traces' };

const PAGE_SIZE = 25;

export default async function TracesPage({ searchParams }) {
  const raw = Number((await searchParams)?.page ?? '1');
  const page = Number.isInteger(raw) && raw >= 1 && raw <= 10_000 ? raw : 1;
  const result = await api.traces(PAGE_SIZE, (page - 1) * PAGE_SIZE);
  const pages = result.ok ? Math.max(1, Math.ceil(result.data.total / PAGE_SIZE)) : 1;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Observability"
        title="Agent traces"
        description="One trace per agent thread (run plus every resume), newest first. Traces hold span metadata and safe execution summaries — never prompts, model output, or chain-of-thought. Server paths are removed by the backend."
        meta={result.ok ? `${formatCount(result.data.total)} recorded` : null}
      />
      <Panel>
        {!result.ok ? (
          <div className="p-4"><ErrorNotice code={result.error} /></div>
        ) : result.data.total === 0 ? (
          <EmptyState title="No agent runs recorded" />
        ) : (
          <TraceTable traces={result.data.traces} />
        )}
      </Panel>
      {result.ok && pages > 1 ? (
        <nav aria-label="Pagination" className="flex items-center justify-between font-mono text-xs text-ink-muted">
          {page > 1 ? <Link href={`/traces?page=${page - 1}`} className="text-accent hover:underline">← newer</Link> : <span />}
          <span>page {page} / {pages}</span>
          {page < pages ? <Link href={`/traces?page=${page + 1}`} className="text-accent hover:underline">older →</Link> : <span />}
        </nav>
      ) : null}
    </div>
  );
}
