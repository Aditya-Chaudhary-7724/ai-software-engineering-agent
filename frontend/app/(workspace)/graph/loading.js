export default function GraphLoading() {
  return (
    <div role="status" className="space-y-3">
      <div className="h-8 w-56 animate-pulse bg-sunken motion-reduce:animate-none" />
      <div className="h-[34rem] border border-rule bg-surface p-4 text-sm text-ink-muted">Loading the knowledge graph…</div>
    </div>
  );
}
