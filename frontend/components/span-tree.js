'use client';

import { ChevronRight } from 'lucide-react';
import { AnimatePresence, MotionConfig, motion } from 'motion/react';
import { useState } from 'react';

import { formatDuration } from '@/lib/format';
import { buildSpanTree } from '@/lib/span-tree';

import { StatusDot, Tag } from './ui';

/**
 * Interactive span tree + waterfall. Motion is used for exactly one thing:
 * expanding/collapsing a subtree, so the eye can follow where rows went.
 * `reducedMotion="user"` disables it for operators who ask for less motion.
 * Span data is already sanitized server-side (no absolute paths, secrets
 * redacted); it is rendered as text only.
 */

function formatValue(value) {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function SpanRow({ node, depth, selectedId, onSelect, collapsed, onToggle }) {
  const { span } = node;
  const hasChildren = node.children.length > 0;
  const isCollapsed = collapsed.has(span.span_id);
  const selected = selectedId === span.span_id;

  return (
    <li>
      <div className={`grid grid-cols-[minmax(0,1fr)_minmax(8rem,38%)] items-center border-b border-rule text-sm ${selected ? 'bg-accent-soft' : 'hover:bg-sunken'}`}>
        <div className="flex min-w-0 items-center gap-1.5 py-1.5 pr-3" style={{ paddingLeft: `${depth * 1.125 + 0.5}rem` }}>
          {hasChildren ? (
            <button
              type="button"
              onClick={() => onToggle(span.span_id)}
              aria-expanded={!isCollapsed}
              aria-label={`${isCollapsed ? 'Expand' : 'Collapse'} ${span.name}`}
              className="text-ink-faint hover:text-ink"
            >
              <motion.span animate={{ rotate: isCollapsed ? 0 : 90 }} transition={{ duration: 0.15 }} className="block">
                <ChevronRight aria-hidden size={14} />
              </motion.span>
            </button>
          ) : (
            <span className="inline-block w-3.5" />
          )}
          <button type="button" onClick={() => onSelect(span.span_id)} className="flex min-w-0 items-center gap-2 text-left" aria-pressed={selected}>
            <StatusDot tone={span.status} />
            <span className="truncate font-mono text-[0.8125rem]">{span.name}</span>
            <Tag>{span.kind}</Tag>
          </button>
        </div>
        <div className="relative h-full border-l border-rule px-2 py-1.5">
          <div className="relative h-3">
            <span
              className={`absolute top-0.5 h-2 ${span.status === 'error' ? 'bg-err' : span.status === 'interrupted' ? 'bg-warn' : 'bg-ink-faint'}`}
              style={{ left: `${node.offsetPct}%`, width: `${node.widthPct}%` }}
            />
          </div>
          <span className="absolute right-2 top-1/2 -translate-y-1/2 font-mono text-[0.6875rem] text-ink-muted">{formatDuration(span.duration_ms)}</span>
        </div>
      </div>
      <AnimatePresence initial={false}>
        {hasChildren && !isCollapsed ? (
          <motion.ul
            key="children"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: [0.2, 0, 0, 1] }}
            className="overflow-hidden"
          >
            {node.children.map((child) => (
              <SpanRow key={child.span.span_id} node={child} depth={depth + 1} selectedId={selectedId} onSelect={onSelect} collapsed={collapsed} onToggle={onToggle} />
            ))}
          </motion.ul>
        ) : null}
      </AnimatePresence>
    </li>
  );
}

function SpanDetail({ span }) {
  if (!span) return <p className="p-4 text-sm text-ink-muted">Select a span to inspect its recorded attributes.</p>;
  const attributes = Object.entries(span.attributes);
  return (
    <div className="space-y-4 p-4 text-sm">
      <div>
        <p className="font-mono text-[0.8125rem] font-medium">{span.name}</p>
        <p className="mt-0.5 font-mono text-[0.6875rem] text-ink-faint">{span.span_id}</p>
      </div>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
        <dt className="text-ink-muted">status</dt><dd><StatusDot tone={span.status} label={span.status} /></dd>
        <dt className="text-ink-muted">kind</dt><dd className="font-mono text-xs">{span.kind}</dd>
        <dt className="text-ink-muted">duration</dt><dd className="font-mono text-xs">{formatDuration(span.duration_ms)}</dd>
      </dl>
      {span.error ? (
        <div className="border-l-2 border-err px-3 py-1.5">
          <p className="label-micro !text-err">error</p>
          <p className="mt-1 break-words font-mono text-xs">{span.error}</p>
        </div>
      ) : null}
      <div>
        <p className="label-micro mb-1.5">attributes</p>
        {attributes.length === 0 ? (
          <p className="text-ink-faint">none recorded</p>
        ) : (
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1">
            {attributes.map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="font-mono text-xs text-ink-muted">{key}</dt>
                <dd className="break-words font-mono text-xs">{formatValue(value)}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
      {span.events.length ? (
        <div>
          <p className="label-micro mb-1.5">events</p>
          <ul className="space-y-1 font-mono text-xs">
            {span.events.map((e, i) => (
              <li key={i}>{e.name} <span className="text-ink-faint">{formatValue(e.attributes)}</span></li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export default function SpanTree({ spans }) {
  const { roots, totalMs } = buildSpanTree(spans);
  const [selectedId, setSelectedId] = useState(() => spans.find((s) => s.status === 'error')?.span_id ?? null);
  const [collapsed, setCollapsed] = useState(() => new Set());
  const toggle = (id) =>
    setCollapsed((current) => {
      const next = new Set(current);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  return (
    <MotionConfig reducedMotion="user">
      <div className="grid xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0">
          <div className="grid grid-cols-[minmax(0,1fr)_minmax(8rem,38%)] border-b border-rule">
            <span className="label-micro px-4 py-2">span</span>
            <span className="label-micro border-l border-rule px-2 py-2">timeline · {formatDuration(totalMs)}</span>
          </div>
          <ul>
            {roots.map((node) => (
              <SpanRow key={node.span.span_id} node={node} depth={0} selectedId={selectedId} onSelect={setSelectedId} collapsed={collapsed} onToggle={toggle} />
            ))}
          </ul>
        </div>
        <aside className="border-t border-rule xl:border-l xl:border-t-0">
          <SpanDetail span={spans.find((s) => s.span_id === selectedId)} />
        </aside>
      </div>
    </MotionConfig>
  );
}
