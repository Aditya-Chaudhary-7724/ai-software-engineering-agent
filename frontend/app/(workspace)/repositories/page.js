import Link from 'next/link';

import AddRepositoryForm from '@/components/add-repository-form';
import JobProgress from '@/components/job-progress';
import { EmptyState, ErrorNotice, PageHeader, Panel, StatusDot, Tag } from '@/components/ui';
import { formatCount, formatTimestamp } from '@/lib/format';
import { api } from '@/lib/server/backend';
import { getCsrfToken } from '@/lib/server/dal';

export const metadata = { title: 'Repositories' };

function graphReachable(capabilities) {
  return capabilities.ok && capabilities.data.graph.ok && capabilities.data.graph.detail === 'reachable';
}

export default async function RepositoriesPage() {
  const [result, capabilities, jobs, csrfToken] = await Promise.all([
    api.repositories(200, 0),
    api.capabilities(),
    api.jobs({ limit: 8 }),
    getCsrfToken(),
  ]);
  const ingestionAvailable = capabilities.ok && capabilities.data.features.repository_ingestion;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Workspace"
        title="Repositories"
        description="Repositories indexed into PostgreSQL + pgvector. Add a public GitHub repository by URL; cloning and indexing run as background jobs on the server."
        meta={result.ok ? `${formatCount(result.data.total)} total` : null}
      />
      <Panel title="Add a GitHub repository">
        {!capabilities.ok ? (
          <div className="p-4"><ErrorNotice code={capabilities.error} /></div>
        ) : ingestionAvailable ? (
          <AddRepositoryForm csrfToken={csrfToken} graphAvailable={graphReachable(capabilities)} />
        ) : (
          <EmptyState title="Ingestion is not available on this backend">
            The backend needs DATABASE_URL and an absolute WORKSPACE_ROOT, and its background worker must be running (see the backend README).
          </EmptyState>
        )}
      </Panel>

      {jobs.ok && jobs.data.jobs.length ? (
        <Panel title="Recent jobs" meta="newest first">
          <div className="space-y-3 p-4">
            {jobs.data.jobs.map((job) => <JobProgress key={job.id} initialJob={job} />)}
          </div>
        </Panel>
      ) : !jobs.ok && jobs.error !== 'schema_not_initialized' ? (
        <ErrorNotice code={jobs.error} compact />
      ) : null}

      <Panel title="Indexed repositories">
        {!result.ok ? (
          <div className="p-4"><ErrorNotice code={result.error} /></div>
        ) : result.data.total === 0 ? (
          <EmptyState title="No repositories indexed yet">Add one above by its GitHub URL.</EmptyState>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-rule text-left">
                {['Repository', 'Languages', 'Chunks', 'Files', 'First ingested', 'Files on disk'].map((h) => (
                  <th key={h} scope="col" className="label-micro px-4 py-2 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.data.repositories.map((r) => (
                <tr key={r.id} className="border-b border-rule last:border-0 hover:bg-sunken">
                  <td className="px-4 py-2.5">
                    <Link href={`/repositories/${r.id}`} className="font-medium hover:text-accent">{r.github_full_name ?? r.name}</Link>
                    <span className="ml-2 font-mono text-[0.6875rem] text-ink-faint">#{r.id}</span>
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="flex flex-wrap gap-1">{r.languages.length ? r.languages.map((l) => <Tag key={l}>{l}</Tag>) : <span className="text-ink-faint">—</span>}</span>
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs">{formatCount(r.chunk_count)}</td>
                  <td className="px-4 py-2.5 font-mono text-xs">{formatCount(r.indexed_file_count)}</td>
                  <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs text-ink-muted">{formatTimestamp(r.first_ingested_at)}</td>
                  <td className="px-4 py-2.5 text-xs">
                    <StatusDot tone={r.available_on_disk ? 'ok' : 'warn'} label={r.available_on_disk ? 'present' : 'missing'} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
