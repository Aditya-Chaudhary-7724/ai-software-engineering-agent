import Link from 'next/link';

import TraceTable from '@/components/trace-table';
import { EmptyState, ErrorNotice, PageHeader, Panel, StatusDot } from '@/components/ui';
import { formatCount, formatTimestamp } from '@/lib/format';
import { api } from '@/lib/server/backend';

export const metadata = { title: 'Dashboard' };

const FEATURE_LABELS = {
  repository_browsing: 'Repository browsing',
  file_browsing: 'File browsing',
  graph_summary: 'Graph summary',
  trace_viewing: 'Trace viewing',
  repository_ingestion: 'Ingestion over HTTP',
  ask: 'Ask (read-only RAG)',
  agent_execution: 'Agent tasks',
  approvals: 'Human approval',
  test_execution: 'Sandbox test runs',
  evaluation_runs: 'Evaluation runs',
  github_push_and_pull_requests: 'GitHub push / PR',
};

export default async function DashboardPage() {
  const [capabilities, repositories, traces] = await Promise.all([api.capabilities(), api.repositories(5, 0), api.traces(8, 0)]);

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Workspace" title="Dashboard" description="Live state of the backend, indexed repositories, and recorded agent runs. Every value below comes from the backend API." />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <Panel
          title="Repositories"
          meta={repositories.ok ? `${formatCount(repositories.data.total)} indexed` : null}
          bodyClassName={repositories.ok && repositories.data.total ? '' : 'p-0'}
        >
          {!repositories.ok ? (
            <div className="p-4"><ErrorNotice code={repositories.error} /></div>
          ) : repositories.data.total === 0 ? (
            <EmptyState title="No repositories indexed yet">
              Add a GitHub repository from the <Link href="/repositories" className="text-accent hover:underline">Repositories</Link> page.
            </EmptyState>
          ) : (
            <ul>
              {repositories.data.repositories.map((r) => (
                <li key={r.id} className="flex items-center justify-between gap-4 border-b border-rule px-4 py-2.5 last:border-0">
                  <Link href={`/repositories/${r.id}`} className="font-medium hover:text-accent">{r.name}</Link>
                  <span className="font-mono text-xs text-ink-muted">
                    {formatCount(r.chunk_count)} chunks · {r.languages.join(', ') || 'no languages'} · {formatTimestamp(r.first_ingested_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Exposed operations" meta="api/v1">
          {capabilities.ok ? (
            <ul className="divide-y divide-rule text-sm">
              {Object.entries(capabilities.data.features).map(([key, enabled]) => (
                <li key={key} className="flex items-center justify-between px-4 py-1.5">
                  <span className={enabled ? '' : 'text-ink-faint'}>{FEATURE_LABELS[key] ?? key}</span>
                  <StatusDot tone={enabled ? 'ok' : 'idle'} label={<span className="font-mono text-[0.6875rem] text-ink-faint">{enabled ? 'on' : 'not yet'}</span>} />
                </li>
              ))}
            </ul>
          ) : (
            <div className="p-4"><ErrorNotice code={capabilities.error} compact /></div>
          )}
        </Panel>
      </div>

      <Panel title="Recent agent runs" meta={traces.ok ? `${formatCount(traces.data.total)} recorded` : null}>
        {!traces.ok ? (
          <div className="p-4"><ErrorNotice code={traces.error} /></div>
        ) : traces.data.total === 0 ? (
          <EmptyState title="No agent runs recorded">Runs are recorded when the agent executes with a JSONFileRecorder (for example via backend/scripts/manual_agent_demo.py).</EmptyState>
        ) : (
          <>
            <TraceTable traces={traces.data.traces} />
            <div className="border-t border-rule px-4 py-2 text-right text-sm">
              <Link href="/traces" className="text-accent hover:underline">All runs →</Link>
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}
