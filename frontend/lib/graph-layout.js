/**
 * Deterministic layout for ONE neighbourhood: the focus node in the
 * centre, nodes with an edge INTO the focus on the left, nodes the focus
 * points TO on the right, grouped by relationship type then label, wrapped
 * into extra columns past `perColumn`. Pure (no randomness, no physics), so
 * the same data always draws the same picture — and it is unit-tested.
 */

const RELATIONSHIP_ORDER = ['CONTAINS', 'DEFINES', 'IMPORTS', 'RESOLVES_TO', 'INHERITS'];

export const LAYOUT = Object.freeze({ columnGap: 320, rowGap: 64, perColumn: 14 });

export function layoutNeighborhood(neighborhood, layout = LAYOUT) {
  const { focus, nodes, edges } = neighborhood;
  const byId = new Map(nodes.map((n) => [n.id, n]));

  // Each neighbour's side and the relationship that put it there.
  const placement = new Map();
  for (const edge of edges) {
    if (edge.target === focus.id && edge.source !== focus.id && !placement.has(edge.source)) {
      placement.set(edge.source, { side: 'in', type: edge.type });
    }
  }
  for (const edge of edges) {
    if (edge.source === focus.id && edge.target !== focus.id) {
      placement.set(edge.target, { side: 'out', type: edge.type }); // outgoing wins if both
    }
  }

  const sideOrder = (id) => {
    const p = placement.get(id);
    return [RELATIONSHIP_ORDER.indexOf(p.type), byId.get(id)?.label ?? ''];
  };
  const compare = (a, b) => {
    const [ta, la] = sideOrder(a);
    const [tb, lb] = sideOrder(b);
    return ta - tb || la.localeCompare(lb);
  };
  const columnsFor = (side) => {
    const ids = [...placement.keys()].filter((id) => placement.get(id).side === side && byId.has(id)).sort(compare);
    const columns = [];
    for (let i = 0; i < ids.length; i += layout.perColumn) columns.push(ids.slice(i, i + layout.perColumn));
    return columns;
  };

  const positions = new Map([[focus.id, { x: 0, y: 0 }]]);
  for (const [side, sign] of [['in', -1], ['out', 1]]) {
    columnsFor(side).forEach((column, c) => {
      column.forEach((id, row) => {
        positions.set(id, { x: sign * layout.columnGap * (c + 1), y: (row - (column.length - 1) / 2) * layout.rowGap });
      });
    });
  }

  return {
    nodes: nodes
      .filter((n) => positions.has(n.id))
      .map((n) => ({
        id: n.id,
        type: 'graphNode',
        position: positions.get(n.id),
        data: { node: n, isFocus: n.id === focus.id, side: n.id === focus.id ? 'focus' : placement.get(n.id).side },
      })),
    edges: edges
      .filter((e) => positions.has(e.source) && positions.has(e.target))
      .map((e) => ({ id: e.id, source: e.source, target: e.target, sourceHandle: 'out', targetHandle: 'in', label: e.type, data: { type: e.type } })),
  };
}
