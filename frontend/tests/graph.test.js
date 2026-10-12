import { readFileSync } from 'node:fs';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { NOT_AVAILABLE_RELATIONSHIPS, RELATIONSHIPS, graphError, graphHref, isValidNodeId, nodeSourceHref } from '@/lib/graph';
import { LAYOUT, layoutNeighborhood } from '@/lib/graph-layout';

vi.mock('@/lib/server/dal', () => ({ requireSession: vi.fn(async () => ({ sid: 'test' })) }));

const id = (n) => `4:0b0b0b0b-0000-4000-8000-000000000000:${n}`;
const node = (n, kind, label, extra = {}) => ({ id: id(n), kind, label, ...extra });

function neighborhood() {
  const focus = node(1, 'Class', 'Impl', { relative_path: 'pkg/impl.py', start_line: 5, end_line: 7 });
  const file = node(2, 'File', 'impl.py', { relative_path: 'pkg/impl.py' });
  const method = node(3, 'Function', 'Impl.run', { relative_path: 'pkg/impl.py', start_line: 6, end_line: 7 });
  const base = node(4, 'Class', 'Base', { relative_path: 'pkg/base.py', start_line: 1, end_line: 3 });
  return {
    focus,
    nodes: [focus, file, method, base],
    edges: [
      { id: 'e1', source: file.id, target: focus.id, type: 'DEFINES' },
      { id: 'e2', source: focus.id, target: method.id, type: 'DEFINES' },
      { id: 'e3', source: focus.id, target: base.id, type: 'INHERITS' },
    ],
  };
}

describe('layoutNeighborhood', () => {
  it('puts the focus in the centre, incoming on the left, outgoing on the right', () => {
    const { nodes } = layoutNeighborhood(neighborhood());
    const pos = Object.fromEntries(nodes.map((n) => [n.data.node.label, n.position]));
    expect(pos.Impl).toEqual({ x: 0, y: 0 });
    expect(pos['impl.py'].x).toBeLessThan(0);
    expect(pos['Impl.run'].x).toBeGreaterThan(0);
    expect(pos.Base.x).toBeGreaterThan(0);
    // grouped by relationship order: DEFINES above INHERITS on the right
    expect(pos['Impl.run'].y).toBeLessThan(pos.Base.y);
  });

  it('is deterministic and draws only edges between returned nodes', () => {
    const data = neighborhood();
    data.edges.push({ id: 'ghost', source: data.focus.id, target: id(99), type: 'CONTAINS' });
    const a = layoutNeighborhood(data);
    const b = layoutNeighborhood(neighborhood());
    expect(a.nodes).toEqual(b.nodes);
    expect(a.edges.map((e) => e.id)).toEqual(['e1', 'e2', 'e3']);
    expect(a.nodes).toHaveLength(4);
  });

  it('keeps every edge label equal to the real relationship type', () => {
    const { edges } = layoutNeighborhood(neighborhood());
    expect(edges.map((e) => e.label)).toEqual(['DEFINES', 'DEFINES', 'INHERITS']);
    expect(edges.every((e) => e.sourceHandle === 'out' && e.targetHandle === 'in')).toBe(true);
  });

  it('wraps large neighbourhoods into extra columns without overlapping positions', () => {
    const focus = node(1, 'Repository', 'repo');
    const children = Array.from({ length: 40 }, (_, i) => node(100 + i, 'File', `f${String(i).padStart(2, '0')}.py`));
    const { nodes } = layoutNeighborhood({
      focus,
      nodes: [focus, ...children],
      edges: children.map((c, i) => ({ id: `c${i}`, source: focus.id, target: c.id, type: 'CONTAINS' })),
    });
    const xs = new Set(nodes.filter((n) => !n.data.isFocus).map((n) => n.position.x));
    expect(xs.size).toBe(Math.ceil(40 / LAYOUT.perColumn));
    const keys = nodes.map((n) => `${n.position.x},${n.position.y}`);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('handles a node with no relationships', () => {
    const focus = node(1, 'Module', 'os');
    expect(layoutNeighborhood({ focus, nodes: [focus], edges: [] })).toEqual({
      nodes: [{ id: focus.id, type: 'graphNode', position: { x: 0, y: 0 }, data: { node: focus, isFocus: true, side: 'focus' } }],
      edges: [],
    });
  });
});

describe('graph helpers', () => {
  it('lists exactly the relationships the builder creates — never CALLS', () => {
    expect(Object.keys(RELATIONSHIPS)).toEqual(['CONTAINS', 'DEFINES', 'IMPORTS', 'RESOLVES_TO', 'INHERITS']);
    expect(Object.keys(RELATIONSHIPS)).not.toContain('CALLS');
    expect(NOT_AVAILABLE_RELATIONSHIPS).toMatch(/CALLS/);
  });

  it('validates node ids like the backend', () => {
    expect(isValidNodeId(id(12))).toBe(true);
    for (const bad of ['1 OR true', 'MATCH (n) RETURN n', '4:x:1', '', undefined, `${id(1)} `]) expect(isValidNodeId(bad)).toBe(false);
  });

  it('links code-bearing nodes to the highlighted source lines', () => {
    expect(nodeSourceHref(3, node(1, 'Class', 'Impl', { relative_path: 'pkg/impl.py', start_line: 5, end_line: 7 }))).toBe(
      '/repositories/3?path=pkg%2Fimpl.py&lines=5-7#L5',
    );
    expect(nodeSourceHref(3, node(2, 'File', 'impl.py', { relative_path: 'pkg/impl.py' }))).toBe('/repositories/3?path=pkg%2Fimpl.py');
    expect(nodeSourceHref(3, node(3, 'Module', 'os'))).toBeNull();
    expect(nodeSourceHref(3, node(4, 'Folder', 'pkg', { relative_path: 'pkg' }))).toBeNull();
  });

  it('builds explorer URLs and explains every graph error code', () => {
    expect(graphHref(3, { node: id(1), q: 'esc ape' })).toBe(`/graph?repository=3&node=${encodeURIComponent(id(1))}&q=esc+ape`);
    for (const code of ['graph_not_configured', 'graph_unavailable', 'graph_not_built', 'node_not_found']) {
      expect(graphError(code).title).not.toBe('Graph request failed');
    }
    expect(graphError('<b>x</b>').title).toBe('Graph request failed');
  });
});

describe('backend client graph calls (server-to-server)', () => {
  beforeEach(() => {
    vi.stubEnv('BACKEND_URL', 'http://127.0.0.1:8000');
    vi.stubEnv('API_SERVICE_TOKEN', 'service-token-'.padEnd(48, 'z'));
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it('sends only a node id and limit, or a search string and limit — never Cypher', async () => {
    const fetchMock = vi.fn(async () => Response.json({}));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('@/lib/server/backend');

    await api.graphNeighborhood(3, { node: id(1), limit: 100 });
    await api.graphSearch(3, "x') DETACH DELETE n //");

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `http://127.0.0.1:8000/api/v1/repositories/3/graph/neighborhood?node=${encodeURIComponent(id(1))}&limit=100`,
    );
    expect(String(fetchMock.mock.calls[1][0])).toBe(
      "http://127.0.0.1:8000/api/v1/repositories/3/graph/search?q=x%27%29+DETACH+DELETE+n+%2F%2F&limit=20",
    );
    expect(fetchMock.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
  });
});

describe('regressions found by driving the graph in headless Chrome (F5)', () => {
  // The explorer is a client component and this project has no DOM test
  // environment; these source-level guards pin the three fixes. Each bug
  // was reproduced in a real browser before being fixed.
  const source = readFileSync(new URL('../components/graph-explorer.js', import.meta.url), 'utf8');

  it('double-click is not swallowed by React Flow’s double-click zoom', () => {
    expect(source).toMatch(/zoomOnDoubleClick=\{false\}/);
    expect(source).toMatch(/onNodeDoubleClick=/);
  });

  it('the viewport re-fits only when the focus node changes — never on selection', () => {
    expect(source).toMatch(/\}, \[focusId, fitView\]\);/);
    expect(source).not.toMatch(/\}, \[flow, fitView\]\);/);
    expect(source).not.toMatch(/^\s*fitView\s*$/m); // no `fitView` prop on <ReactFlow>
  });

  it('selection never rewrites React Flow’s nodes prop (it is read from context)', () => {
    expect(source).toMatch(/useContext\(SelectedNodeContext\)/);
    expect(source).not.toMatch(/selected: n\.id === selectedId/);
  });

  it('colour mode is resolved on the client, not left to SSR "system"', () => {
    expect(source).not.toMatch(/^\s*colorMode="system"/m); // the JSX prop, not the explanatory comment
    expect(source).toMatch(/^\s*colorMode=\{colorMode\}/m);
    expect(source).toMatch(/prefers-color-scheme: dark/);
  });
});
