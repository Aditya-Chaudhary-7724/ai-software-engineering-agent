/**
 * Pure helpers for the knowledge-graph explorer (F5). Everything shown is
 * derived from the backend's response; the relationship list mirrors what
 * GraphBuilder actually creates — there is no CALLS relationship.
 */

export const RELATIONSHIPS = {
  CONTAINS: 'Repository/folder contains a folder or file',
  DEFINES: 'File defines a class or function; class defines a method',
  IMPORTS: 'File imports a module',
  RESOLVES_TO: 'Imported module resolves to a file in this repository',
  INHERITS: 'Class inherits from another class (only when unambiguous)',
};

export const NOT_AVAILABLE_RELATIONSHIPS = 'CALLS / USES are not available: call sites are not extracted by the parser, so they are never shown or approximated.';

// Neo4j 5 element ids: "<n>:<database uuid>:<n>" (validated again server-side).
const NODE_ID = /^\d{1,3}:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:\d{1,19}$/;

export function isValidNodeId(value) {
  return typeof value === 'string' && NODE_ID.test(value);
}

/** File-viewer link for nodes that map to code; null otherwise. */
export function nodeSourceHref(repositoryId, node) {
  if (!node.relative_path || !['File', 'Class', 'Function'].includes(node.kind)) return null;
  const params = new URLSearchParams({ path: node.relative_path });
  if (Number.isInteger(node.start_line) && Number.isInteger(node.end_line)) {
    params.set('lines', `${node.start_line}-${node.end_line}`);
    return `/repositories/${repositoryId}?${params.toString()}#L${node.start_line}`;
  }
  return `/repositories/${repositoryId}?${params.toString()}`;
}

export function graphHref(repositoryId, { node, q } = {}) {
  const params = new URLSearchParams({ repository: String(repositoryId) });
  if (node) params.set('node', node);
  if (q) params.set('q', q);
  return `/graph?${params.toString()}`;
}

const GRAPH_ERRORS = {
  graph_not_configured: ['Knowledge graph not configured', 'Neo4j is optional and is not configured on the backend (NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD).'],
  graph_unavailable: ['Knowledge graph unavailable', 'Neo4j is configured but not reachable right now. Nothing is shown rather than stale or invented data.'],
  graph_not_built: ['No graph built for this repository', 'Re-index it with “Rebuild the knowledge graph” checked (Repositories → this repository → Index).'],
  node_not_found: ['Node not found', 'That node is not part of this repository’s graph (it may have changed after a rebuild).'],
  invalid_node_id: ['Invalid node id', 'Graph node ids come from the explorer itself.'],
  invalid_query: ['Invalid search', 'Search text cannot contain control characters.'],
};

export function graphError(code) {
  const [title, detail] = GRAPH_ERRORS[code] ?? ['Graph request failed', 'The backend returned an unexpected error.'];
  return { title, detail };
}
