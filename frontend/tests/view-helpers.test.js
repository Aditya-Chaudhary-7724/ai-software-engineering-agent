import { describe, expect, it } from 'vitest';

import { buildFileTree } from '@/lib/file-tree';
import { buildSpanTree } from '@/lib/span-tree';

describe('buildFileTree', () => {
  it('nests files under their folders', () => {
    const tree = buildFileTree([
      { relative_path: 'README.md' },
      { relative_path: 'src/app.py' },
      { relative_path: 'src/lib/util.py' },
    ]);
    expect(tree.files.map((f) => f.name)).toEqual(['README.md']);
    const src = tree.folders.get('src');
    expect(src.path).toBe('src');
    expect(src.files.map((f) => f.name)).toEqual(['app.py']);
    expect(src.folders.get('lib').files[0].relative_path).toBe('src/lib/util.py');
  });
});

describe('buildSpanTree', () => {
  const span = (id, parent, start, ms, status = 'ok') => ({
    span_id: id, parent_span_id: parent, name: id, kind: 'node', status,
    start_time: `2026-10-10T10:00:${String(start).padStart(2, '0')}.000+00:00`, duration_ms: ms, attributes: {}, events: [],
  });

  it('nests by parent, orders by start, and computes timeline offsets', () => {
    const { roots, totalMs } = buildSpanTree([span('b', 'root', 5, 5000), span('root', null, 0, 10000), span('a', 'root', 1, 1000)]);
    expect(totalMs).toBe(10000);
    expect(roots.map((r) => r.span.span_id)).toEqual(['root']);
    expect(roots[0].children.map((c) => c.span.span_id)).toEqual(['a', 'b']);
    expect(roots[0].children[1].offsetPct).toBeCloseTo(50);
    expect(roots[0].children[1].widthPct).toBeCloseTo(50);
  });

  it('keeps orphaned spans visible as roots instead of dropping them', () => {
    const { roots } = buildSpanTree([span('orphan', 'missing-parent', 0, 10)]);
    expect(roots).toHaveLength(1);
  });
});
