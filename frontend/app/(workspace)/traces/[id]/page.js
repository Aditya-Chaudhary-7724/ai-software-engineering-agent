import Link from 'next/link';
import { notFound } from 'next/navigation';

import SpanTree from '@/components/span-tree';
import { ErrorNotice, KeyValue, PageHeader, Panel, StatusDot } from '@/components/ui';
import { formatDuration, formatTimestamp } from '@/lib/format';
import { api } from '@/lib/server/backend';

export const metadata = { title: 'Trace' };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export default async function TracePage({ params }) {
  const { id } = await params;
  if (!UUID.test(id)) notFound();
  const result = await api.trace(id);
  if (!result.ok && result.error === 'trace_not_found') notFound();

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow={<Link href="/traces" className="hover:text-accent">Agent traces</Link>}
        title={result.ok ? result.data.question || 'Agent run' : 'Agent run'}
        meta={id}
      />
      {!result.ok ? (
        <ErrorNotice code={result.error} />
      ) : (
        <>
          <Panel bodyClassName="p-4">
            <KeyValue
              items={[
                ['status', <StatusDot key="s" tone={result.data.status} label={result.data.status.replace('_', ' ')} />],
                ['started', formatTimestamp(result.data.started_at)],
                ['duration', formatDuration(result.data.duration_ms)],
                ['repository', result.data.repository_id !== null ? (
                  <Link key="r" href={`/repositories/${result.data.repository_id}`} className="text-accent hover:underline">#{result.data.repository_id}</Link>
                ) : '—'],
                ['spans', `${result.data.span_count}${result.data.error_span_count ? ` (${result.data.error_span_count} with errors)` : ''}`],
              ]}
            />
          </Panel>
          <Panel title="Execution" meta="span tree · select a span for detail">
            <SpanTree spans={result.data.spans} />
          </Panel>
        </>
      )}
    </div>
  );
}
