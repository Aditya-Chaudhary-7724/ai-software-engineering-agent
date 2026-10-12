import Link from 'next/link';

import GraphExplorer from '@/components/graph-explorer';
import { EmptyState, ErrorNotice, PageHeader, Panel, Tag } from '@/components/ui';
import { graphError, graphHref, isValidNodeId } from '@/lib/graph';
import { api } from '@/lib/server/backend';

export const metadata = { title: 'Knowledge graph' };

const GRAPH_CODES = new Set(['graph_not_configured', 'graph_unavailable', 'graph_not_built', 'node_not_found', 'invalid_node_id', 'invalid_query']);

function GraphNotice({ code }) {
  if (!GRAPH_CODES.has(code)) return <ErrorNotice code={code} />;
  const { title, detail } = graphError(code);
  const tone = code === 'graph_unavailable' || code === 'node_not_found' ? 'border-err bg-err/5' : 'border-warn bg-warn/5';
  return (
    <div role="alert" className={`border-l-2 px-4 py-3 ${tone}`}>
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-0.5 text-sm text-ink-muted">{detail}</p>
      <p className="mt-1 font-mono text-[0.6875rem] text-ink-faint">code: {code}</p>
    </div>
  );
}

export default async function GraphPage({ searchParams }) {
  const query = (await searchParams) ?? {};
  const repositories = await api.repositories(200, 0);
  if (!repositories.ok) {
    return (
      <div className="space-y-6">
        <PageHeader eyebrow="Workspace" title="Knowledge graph" />
        <ErrorNotice code={repositories.error} />
      </div>
    );
  }
  const list = repositories.data.repositories;
  const repository = list.find((r) => String(r.id) === query.repository) ?? list[0];
  const node = typeof query.node === 'string' && query.node ? query.node : undefined;
  const q = typeof query.q === 'string' ? query.q.trim().slice(0, 100) : '';

  const [neighborhood, search] = repository
    ? await Promise.all([
        node && !isValidNodeId(node)
          ? Promise.resolve({ ok: false, error: 'invalid_node_id' })
          : api.graphNeighborhood(repository.id, { node, limit: 100 }),
        q ? api.graphSearch(repository.id, q, 20) : Promise.resolve(null),
      ])
    : [null, null];

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Workspace"
        title="Knowledge graph"
        description="Real nodes and relationships from Neo4j, one hop at a time. Files, folders, classes, functions and imported modules — built from parsed definitions and imports. Read-only."
      />

      {list.length === 0 ? (
        <EmptyState title="No repositories yet">
          <Link href="/repositories" className="text-accent hover:underline">Add a repository</Link> with “Also build the knowledge graph” checked.
        </EmptyState>
      ) : (
        <>
          <div className="grid gap-3 border border-rule bg-surface p-4 md:grid-cols-[16rem_minmax(0,1fr)_auto] md:items-end">
            <form action="/graph" className="contents">
              <div>
                <label htmlFor="graph-repository" className="label-micro mb-1.5 block">Repository</label>
                <select id="graph-repository" name="repository" defaultValue={String(repository.id)} className="block w-full border border-rule-strong bg-surface px-2 py-2 text-sm">
                  {list.map((r) => (
                    <option key={r.id} value={String(r.id)}>{r.github_full_name ?? r.name} (#{r.id})</option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="graph-search" className="label-micro mb-1.5 block">Find a class, function, module or file</label>
                <input id="graph-search" name="q" defaultValue={q} maxLength={100} placeholder="escape" className="block w-full border border-rule-strong bg-surface px-3 py-2 font-mono text-sm" />
              </div>
              <button type="submit" className="bg-ink px-4 py-2 text-sm font-medium text-paper">Search</button>
            </form>
          </div>

          {search ? (
            <Panel title="Search results" meta={search.ok ? `${search.data.results.length} match${search.data.results.length === 1 ? '' : 'es'} (max ${search.data.limit})` : null}>
              {!search.ok ? (
                <div className="p-4"><GraphNotice code={search.error} /></div>
              ) : search.data.results.length === 0 ? (
                <EmptyState title={`No graph nodes match “${search.data.query}”`} />
              ) : (
                <ul className="divide-y divide-rule">
                  {search.data.results.map((n) => (
                    <li key={n.id}>
                      <Link href={graphHref(repository.id, { node: n.id, q })} className="flex items-center gap-3 px-4 py-2 text-sm hover:bg-sunken">
                        <Tag>{n.kind}</Tag>
                        <span className="font-mono">{n.label}</span>
                        {n.relative_path && n.kind !== 'File' ? <span className="truncate font-mono text-xs text-ink-faint">{n.relative_path}</span> : null}
                        {n.kind === 'File' ? <span className="truncate font-mono text-xs text-ink-faint">{n.relative_path}</span> : null}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          ) : null}

          {!neighborhood.ok ? (
            <GraphNotice code={neighborhood.error} />
          ) : (
            <Panel
              title={`Neighbourhood of ${neighborhood.data.focus.kind.toLowerCase()} ${neighborhood.data.focus.label}`}
              meta={`${neighborhood.data.nodes.length - 1} of ${neighborhood.data.total_neighbors} neighbours`}
            >
              {neighborhood.data.truncated ? (
                <p className="border-b border-rule bg-warn/5 px-4 py-1.5 text-xs text-warn">
                  Showing the first {neighborhood.data.limit} of {neighborhood.data.total_neighbors} neighbours (server-side limit). Use search to jump to a specific node.
                </p>
              ) : null}
              {neighborhood.data.total_neighbors === 0 ? (
                <p className="border-b border-rule px-4 py-1.5 text-xs text-ink-muted">This node has no relationships of the supported types.</p>
              ) : null}
              {node ? (
                <p className="border-b border-rule px-4 py-1.5 text-xs">
                  <Link href={graphHref(repository.id)} className="text-accent hover:underline">← Repository overview</Link>
                </p>
              ) : null}
              <GraphExplorer repositoryId={repository.id} neighborhood={neighborhood.data} />
            </Panel>
          )}
        </>
      )}
    </div>
  );
}
