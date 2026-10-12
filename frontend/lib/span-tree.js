/**
 * Turns a trace's flat span list into a parent/child tree with timeline
 * offsets (percent of the trace's total wall time). Pure; spans whose parent
 * is missing become roots rather than disappearing.
 */
export function buildSpanTree(spans) {
  const parse = (iso) => {
    const t = Date.parse(iso);
    return Number.isNaN(t) ? null : t;
  };
  const starts = spans.map((s) => parse(s.start_time)).filter((t) => t !== null);
  const ends = spans.map((s) => parse(s.end_time) ?? (parse(s.start_time) ?? 0) + (s.duration_ms ?? 0)).filter(Boolean);
  const origin = starts.length ? Math.min(...starts) : 0;
  const total = Math.max(1, (ends.length ? Math.max(...ends) : origin) - origin);

  const nodes = new Map(
    spans.map((s) => {
      const start = parse(s.start_time) ?? origin;
      return [
        s.span_id,
        {
          span: s,
          children: [],
          offsetPct: ((start - origin) / total) * 100,
          widthPct: Math.max(0.4, ((s.duration_ms ?? 0) / total) * 100),
        },
      ];
    }),
  );
  const roots = [];
  for (const node of nodes.values()) {
    const parent = node.span.parent_span_id ? nodes.get(node.span.parent_span_id) : null;
    (parent ? parent.children : roots).push(node);
  }
  const byStart = (a, b) => (a.span.start_time < b.span.start_time ? -1 : a.span.start_time > b.span.start_time ? 1 : 0);
  const sortDeep = (list) => {
    list.sort(byStart);
    list.forEach((n) => sortDeep(n.children));
    return list;
  };
  return { roots: sortDeep(roots), totalMs: total };
}
