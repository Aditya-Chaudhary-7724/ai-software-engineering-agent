import Link from 'next/link';
import { notFound } from 'next/navigation';

import CodeView from '@/components/code-view';
import FileTree from '@/components/file-tree';
import ReindexControl from '@/components/reindex-control';
import { EmptyState, ErrorNotice, KeyValue, PageHeader, Panel, StatusDot } from '@/components/ui';
import { parseLineRange } from '@/lib/ask';
import { buildFileTree } from '@/lib/file-tree';
import { formatCount, formatTimestamp } from '@/lib/format';
import { api } from '@/lib/server/backend';
import { getCsrfToken } from '@/lib/server/dal';

export const metadata = { title: 'Repository' };

function Breakdown({ counts }) {
  const entries = Object.entries(counts);
  const total = entries.reduce((sum, [, n]) => sum + n, 0) || 1;
  if (entries.length === 0) return <p className="text-sm text-ink-faint">No chunks indexed.</p>;
  return (
    <ul className="space-y-1.5">
      {entries.map(([name, count]) => (
        <li key={name} className="grid grid-cols-[7rem_1fr_3rem] items-center gap-3 text-sm">
          <span className="truncate text-ink-muted">{name}</span>
          <span className="h-1 bg-sunken"><span className="block h-1 bg-ink-faint" style={{ width: `${(count / total) * 100}%` }} /></span>
          <span className="text-right font-mono text-xs">{formatCount(count)}</span>
        </li>
      ))}
    </ul>
  );
}

function GraphSummary({ result }) {
  if (!result.ok) return <ErrorNotice code={result.error} compact />;
  const g = result.data;
  if (!g.built) return <p className="text-sm text-ink-muted">No knowledge graph has been built for this repository.</p>;
  return (
    <div className="space-y-3">
      <KeyValue items={[['folders', formatCount(g.folders)], ['files', formatCount(g.files)], ['classes', formatCount(g.classes)], ['functions', formatCount(g.functions)]]} />
      <Link href={`/graph?repository=${g.repository_id}`} className="inline-block text-sm text-accent hover:underline">Explore the graph →</Link>
    </div>
  );
}

export default async function RepositoryPage({ params, searchParams }) {
  const { id } = await params;
  if (!/^[1-9]\d{0,17}$/.test(id)) notFound();
  const query = (await searchParams) ?? {};
  const selected = typeof query.path === 'string' && query.path.length > 0 ? query.path : null;
  const highlight = selected ? parseLineRange(query.lines) : null;

  const repository = await api.repository(id);
  if (!repository.ok && repository.error === 'repository_not_found') notFound();
  if (!repository.ok) {
    return (
      <div className="space-y-6">
        <PageHeader eyebrow="Repository" title={`#${id}`} />
        <ErrorNotice code={repository.error} />
      </div>
    );
  }
  const repo = repository.data;
  const [tree, graph, file, capabilities, activeJobs, csrfToken] = await Promise.all([
    repo.available_on_disk ? api.fileTree(id) : Promise.resolve({ ok: false, error: 'repository_files_unavailable' }),
    api.graphSummary(id),
    selected && repo.available_on_disk ? api.fileContent(id, selected) : Promise.resolve(null),
    api.capabilities(),
    api.jobs({ repositoryId: id, active: true, limit: 1 }),
    getCsrfToken(),
  ]);
  const ingestionAvailable = capabilities.ok && capabilities.data.features.repository_ingestion;
  const graphAvailable = capabilities.ok && capabilities.data.graph.ok && capabilities.data.graph.detail === 'reachable';

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow={<Link href="/repositories" className="hover:text-accent">Repositories</Link>}
        title={repo.github_full_name ?? repo.name}
        meta={`#${repo.id} · first ingested ${formatTimestamp(repo.first_ingested_at)}`}
        description={
          <span className="inline-flex flex-wrap gap-x-5 gap-y-1 font-mono text-xs">
            <span>{formatCount(repo.chunk_count)} chunks</span>
            <span>{formatCount(repo.indexed_file_count)} indexed files</span>
            <StatusDot tone={repo.available_on_disk ? 'ok' : 'warn'} label={repo.available_on_disk ? 'files present on server' : 'files missing on server'} />
          </span>
        }
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_18rem]">
        <Panel title="Files" meta={tree.ok ? `${formatCount(tree.data.total)} files${tree.data.truncated ? ' (truncated)' : ''}` : null} bodyClassName="grid min-h-[28rem] grid-cols-1 md:grid-cols-[16rem_minmax(0,1fr)]">
          <div className="max-h-[70vh] overflow-auto border-b border-rule py-2 md:border-b-0 md:border-r">
            {!tree.ok ? (
              <div className="p-3"><ErrorNotice code={tree.error} compact /></div>
            ) : tree.data.total === 0 ? (
              <EmptyState title="No eligible files" />
            ) : (
              <FileTree tree={buildFileTree(tree.data.files)} repositoryId={repo.id} selectedPath={selected} />
            )}
          </div>
          <div className="min-w-0">
            {file === null ? (
              <EmptyState title="Select a file">
                The tree lists the files ingestion considers relevant. Sensitive files (.env, keys, credentials), binaries, and ignored directories are never listed or served.
              </EmptyState>
            ) : file.ok ? (
              <CodeView file={file.data} highlight={highlight} />
            ) : (
              <div className="p-4"><ErrorNotice code={file.error} /></div>
            )}
          </div>
        </Panel>

        <div className="space-y-6">
          {repo.chunk_count > 0 ? (
            <Link href={`/ask?repository=${repo.id}`} className="flex items-center justify-between border border-rule bg-surface px-4 py-3 text-sm hover:border-accent">
              <span className="font-medium">Ask about this repository</span>
              <span className="text-accent">→</span>
            </Link>
          ) : null}
          <Panel title="Index" bodyClassName="p-4">
            {ingestionAvailable ? (
              <ReindexControl
                repositoryId={repo.id}
                csrfToken={csrfToken}
                graphAvailable={graphAvailable}
                filesAvailable={repo.available_on_disk}
                activeJob={activeJobs.ok ? activeJobs.data.jobs[0] ?? null : null}
              />
            ) : (
              <p className="text-sm text-ink-muted">Re-indexing needs the backend&apos;s ingestion worker (WORKSPACE_ROOT configured).</p>
            )}
          </Panel>
          <Panel title="Knowledge graph" bodyClassName="p-4"><GraphSummary result={graph} /></Panel>
          <Panel title="Chunks by language" bodyClassName="p-4"><Breakdown counts={repo.chunks_by_language} /></Panel>
          <Panel title="Chunks by type" bodyClassName="p-4"><Breakdown counts={repo.chunks_by_type} /></Panel>
        </div>
      </div>
    </div>
  );
}
