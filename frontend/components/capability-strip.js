import { embeddingCapability, llmCapability, sandboxCapability } from '@/lib/capabilities';

import { StatusDot } from './ui';

/** One-line, real-data summary of what this deployment can do right now. */
export default function CapabilityStrip({ result }) {
  if (!result.ok) {
    return (
      <div className="flex items-center gap-2 border-b border-rule bg-err/5 px-6 py-2 text-xs text-err">
        <StatusDot tone="err" />
        Backend status unavailable ({result.error})
      </div>
    );
  }
  const c = result.data;
  const items = [
    ['Database', c.database.ok ? 'ok' : 'err', c.database.detail],
    ['Graph', c.graph.ok ? (c.graph.detail.startsWith('not configured') ? 'idle' : 'ok') : 'err', c.graph.detail],
    ['LLM', ...llmCapability(c)],
    ['Embeddings', ...embeddingCapability(c)],
    ['Sandbox', ...sandboxCapability(c)],
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-b border-rule px-6 py-2 text-xs">
      {items.map(([name, tone, detail]) => (
        <span key={name} className="inline-flex items-center gap-2" title={detail}>
          <StatusDot tone={tone} />
          <span className="text-ink-muted">{name}</span>
          <span className="font-mono text-ink-faint">{detail}</span>
        </span>
      ))}
    </div>
  );
}
