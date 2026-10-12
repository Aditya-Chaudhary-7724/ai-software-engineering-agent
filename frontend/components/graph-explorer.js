'use client';

import '@xyflow/react/dist/style.css';

import { Background, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, useReactFlow } from '@xyflow/react';
import { Crosshair, FileCode2 } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { createContext, useContext, useEffect, useMemo, useState, useTransition } from 'react';

import { NOT_AVAILABLE_RELATIONSHIPS, RELATIONSHIPS, graphHref, nodeSourceHref } from '@/lib/graph';
import { layoutNeighborhood } from '@/lib/graph-layout';

/**
 * Interactive one-hop neighbourhood view. Every node and edge drawn comes
 * from the backend response (`neighborhood`) — nothing is added, inferred,
 * or animated in. Click selects a node (details on the right); double-click
 * or "Focus" navigates to that node's own neighbourhood via the URL, so the
 * server fetches it (bounded) and Back/Forward work.
 */

// Selection is read from context, NOT passed through React Flow's `nodes`
// prop: changing `nodes` on a click let React Flow run a queued fitView
// between the two clicks of a double-click (moving the node away), found
// with headless Chrome in F5 verification. The node array now changes only
// when the neighbourhood itself changes.
const SelectedNodeContext = createContext(null);

function GraphNode({ id, data }) {
  const { node, isFocus } = data;
  const selected = useContext(SelectedNodeContext) === id;
  return (
    <div
      className={`w-56 border bg-surface px-3 py-2 text-left shadow-none ${isFocus ? 'border-accent ring-1 ring-accent' : selected ? 'border-ink' : 'border-rule-strong'}`}
    >
      <Handle type="target" id="in" position={Position.Left} className="!border-0 !bg-rule-strong" />
      <p className="flex items-center justify-between gap-2">
        <span className="font-mono text-[0.625rem] uppercase tracking-wider text-ink-faint">{node.kind}</span>
        {isFocus ? <span className="font-mono text-[0.625rem] text-accent">focus</span> : null}
      </p>
      <p className="truncate font-mono text-[0.8125rem] text-ink" title={node.label}>{node.label}</p>
      {node.relative_path && node.kind !== 'File' && node.kind !== 'Folder' ? (
        <p className="truncate font-mono text-[0.6875rem] text-ink-faint" title={node.relative_path}>{node.relative_path}</p>
      ) : null}
      <Handle type="source" id="out" position={Position.Right} className="!border-0 !bg-rule-strong" />
    </div>
  );
}

const NODE_TYPES = { graphNode: GraphNode };

function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

/**
 * React Flow's `colorMode="system"` resolves to "light" during server
 * rendering and did not switch after hydration (seen in F5 verification:
 * white controls on the dark theme). Resolve it on the client, live.
 */
function usePreferredColorMode() {
  const [mode, setMode] = useState('light');
  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const update = () => setMode(query.matches ? 'dark' : 'light');
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  return mode;
}

function Canvas({ flow, focusId, onSelect, onFocus }) {
  const { fitView } = useReactFlow();
  const colorMode = usePreferredColorMode();
  // Re-centre ONLY when the focus node changes (orientation, not decoration).
  // Depending on `flow` here was a bug: selecting a node rebuilds `flow`, so
  // every click re-fit the viewport — moving nodes out from under the second
  // click of a double-click (found with headless Chrome in F5 verification).
  useEffect(() => {
    const id = requestAnimationFrame(() => fitView({ padding: 0.15, duration: prefersReducedMotion() ? 0 : 250 }));
    return () => cancelAnimationFrame(id);
  }, [focusId, fitView]);

  return (
    <ReactFlow
      nodes={flow.nodes}
      edges={flow.edges}
      nodeTypes={NODE_TYPES}
      defaultEdgeOptions={{
        markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
        labelStyle: { fontFamily: 'var(--font-mono)', fontSize: 10, fill: 'var(--color-ink-muted)' },
        labelBgStyle: { fill: 'var(--color-surface)' },
        style: { stroke: 'var(--color-rule-strong)', strokeWidth: 1.25 },
      }}
      onNodeClick={(_, n) => onSelect(n.id)}
      onNodeDoubleClick={(_, n) => onFocus(n.id)}
      onPaneClick={() => onSelect(null)}
      colorMode={colorMode}
      // Double-click means "focus this node" here. React Flow's default
      // double-click-to-zoom handler stopped the dblclick before it reached
      // onNodeDoubleClick (found with headless Chrome in F5 verification);
      // zoom remains available via the controls and the scroll wheel.
      zoomOnDoubleClick={false}
      nodesDraggable={false}
      nodesConnectable={false}
      edgesFocusable={false}
      minZoom={0.2}
      maxZoom={1.75}
    >
      <Background color="var(--color-rule)" gap={24} size={1} />
      <Controls showInteractive={false} position="bottom-left" />
    </ReactFlow>
  );
}

function NodeDetails({ repositoryId, node, edges, nodesById, onFocus, isFocus }) {
  if (!node) return <p className="p-4 text-sm text-ink-muted">Select a node to see its details. Double-click a node to explore its neighbourhood.</p>;
  const source = nodeSourceHref(repositoryId, node);
  const related = edges.filter((e) => e.source === node.id || e.target === node.id);
  return (
    <div className="space-y-4 p-4 text-sm">
      <div>
        <p className="font-mono text-[0.625rem] uppercase tracking-wider text-ink-faint">{node.kind}</p>
        <p className="mt-0.5 break-words font-mono text-[0.875rem] font-medium">{node.label}</p>
      </div>
      <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1">
        {node.relative_path ? (<><dt className="text-ink-muted">path</dt><dd className="break-all font-mono text-xs">{node.relative_path}</dd></>) : null}
        {node.qualified_name ? (<><dt className="text-ink-muted">qualified</dt><dd className="break-all font-mono text-xs">{node.qualified_name}</dd></>) : null}
        {Number.isInteger(node.start_line) ? (<><dt className="text-ink-muted">lines</dt><dd className="font-mono text-xs">{node.start_line}–{node.end_line}</dd></>) : null}
        {node.language ? (<><dt className="text-ink-muted">language</dt><dd className="font-mono text-xs">{node.language}</dd></>) : null}
      </dl>
      {node.kind === 'Module' ? (
        <p className="text-xs text-ink-muted">
          {related.some((e) => e.type === 'RESOLVES_TO' && e.source === node.id)
            ? 'Resolves to a file in this repository (see RESOLVES_TO).'
            : 'No RESOLVES_TO edge in this view: external package, or an import the resolver could not match.'}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {!isFocus ? (
          <button type="button" onClick={() => onFocus(node.id)} className="inline-flex items-center gap-1.5 border border-rule-strong px-2.5 py-1 text-xs hover:bg-sunken">
            <Crosshair aria-hidden size={13} /> Focus
          </button>
        ) : null}
        {source ? (
          <Link href={source} className="inline-flex items-center gap-1.5 border border-rule-strong px-2.5 py-1 text-xs text-accent hover:bg-sunken">
            <FileCode2 aria-hidden size={13} /> Open source
          </Link>
        ) : null}
      </div>
      {related.length ? (
        <div>
          <p className="label-micro mb-1.5">relationships in this view</p>
          <ul className="space-y-1 font-mono text-xs">
            {related.map((e) => {
              const other = nodesById.get(e.source === node.id ? e.target : e.source);
              return (
                <li key={e.id} className="break-words">
                  {e.source === node.id ? '→' : '←'} <span className="text-ink-muted">{e.type}</span> {other?.label}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export default function GraphExplorer({ repositoryId, neighborhood }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [selectedId, setSelectedId] = useState(neighborhood.focus.id);
  const flow = useMemo(() => layoutNeighborhood(neighborhood), [neighborhood]);
  const nodesById = useMemo(() => new Map(neighborhood.nodes.map((n) => [n.id, n])), [neighborhood]);

  useEffect(() => setSelectedId(neighborhood.focus.id), [neighborhood.focus.id]);

  const focus = (id) => {
    if (id === neighborhood.focus.id) return;
    startTransition(() => router.push(graphHref(repositoryId, { node: id })));
  };
  const selectedNode = nodesById.get(selectedId) ?? null;

  return (
    <div className="grid xl:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="relative h-[34rem] border-b border-rule xl:border-b-0 xl:border-r" aria-busy={pending}>
        <SelectedNodeContext.Provider value={selectedId}>
          <ReactFlowProvider>
            <Canvas flow={flow} focusId={neighborhood.focus.id} onSelect={setSelectedId} onFocus={focus} />
          </ReactFlowProvider>
        </SelectedNodeContext.Provider>
        {pending ? (
          <div role="status" className="absolute inset-x-0 top-0 border-b border-rule bg-surface/90 px-4 py-1.5 text-xs text-ink-muted">
            Loading neighbourhood…
          </div>
        ) : null}
      </div>
      <aside className="flex flex-col">
        <NodeDetails
          repositoryId={repositoryId}
          node={selectedNode}
          edges={neighborhood.edges}
          nodesById={nodesById}
          onFocus={focus}
          isFocus={selectedId === neighborhood.focus.id}
        />
        <div className="mt-auto border-t border-rule p-4">
          <p className="label-micro mb-2">relationships</p>
          <dl className="space-y-1 text-xs">
            {Object.entries(RELATIONSHIPS).map(([type, text]) => (
              <div key={type}>
                <dt className="inline font-mono text-ink">{type}</dt> <dd className="inline text-ink-muted">— {text}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-xs text-ink-faint">{NOT_AVAILABLE_RELATIONSHIPS}</p>
        </div>
      </aside>
    </div>
  );
}
