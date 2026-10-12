import { describeError } from '@/lib/error-messages';

/** Small, presentation-only building blocks shared by every screen. */

export function PageHeader({ eyebrow, title, description, meta }) {
  return (
    <header className="border-b border-rule pb-5">
      {eyebrow ? <p className="label-micro mb-2">{eyebrow}</p> : null}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-[1.625rem] font-semibold leading-tight tracking-[-0.015em]">{title}</h1>
        {meta ? <div className="font-mono text-xs text-ink-muted">{meta}</div> : null}
      </div>
      {description ? <p className="mt-2 max-w-3xl text-sm text-ink-muted">{description}</p> : null}
    </header>
  );
}

export function Panel({ title, meta, children, className = '', bodyClassName = '' }) {
  return (
    <section className={`border border-rule bg-surface ${className}`}>
      {title ? (
        <div className="flex items-baseline justify-between gap-3 border-b border-rule px-4 py-2.5">
          <h2 className="label-micro !text-ink-muted">{title}</h2>
          {meta ? <span className="font-mono text-[0.6875rem] text-ink-faint">{meta}</span> : null}
        </div>
      ) : null}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}

const TONES = {
  ok: 'bg-ok',
  completed: 'bg-ok',
  warn: 'bg-warn',
  interrupted: 'bg-warn',
  awaiting_approval: 'bg-warn',
  err: 'bg-err',
  error: 'bg-err',
  failed: 'bg-err',
  running: 'bg-accent',
  idle: 'bg-idle',
};

export function StatusDot({ tone, label }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span aria-hidden className={`inline-block size-1.5 rounded-full ${TONES[tone] ?? 'bg-idle'}`} />
      {label ? <span>{label}</span> : <span className="sr-only">{tone}</span>}
    </span>
  );
}

export function Tag({ children, tone = 'neutral' }) {
  const styles = {
    neutral: 'border-rule-strong text-ink-muted',
    accent: 'border-accent/40 text-accent',
    warn: 'border-warn/40 text-warn',
  };
  return (
    <span className={`inline-flex items-center border px-1.5 py-px font-mono text-[0.6875rem] leading-4 ${styles[tone]}`}>
      {children}
    </span>
  );
}

export function EmptyState({ title, children }) {
  return (
    <div className="px-4 py-8">
      <p className="text-sm font-medium">{title}</p>
      {children ? <div className="mt-1 max-w-xl text-sm text-ink-muted">{children}</div> : null}
    </div>
  );
}

export function ErrorNotice({ code, compact = false }) {
  const { title, detail } = describeError(code);
  return (
    <div role="alert" className={`border-l-2 border-err bg-err/5 ${compact ? 'px-3 py-2' : 'px-4 py-3'}`}>
      <p className="text-sm font-medium text-err">{title}</p>
      <p className="mt-0.5 text-sm text-ink-muted">{detail}</p>
      <p className="mt-1 font-mono text-[0.6875rem] text-ink-faint">code: {code}</p>
    </div>
  );
}

export function KeyValue({ items }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 text-sm">
      {items.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-ink-muted">{key}</dt>
          <dd className="font-mono text-[0.8125rem]">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
