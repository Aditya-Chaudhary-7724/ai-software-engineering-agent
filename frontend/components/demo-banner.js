import { Eye } from 'lucide-react';

import { DEMO_BANNER_TEXT } from '@/lib/demo';

/** Shown on every page of a "View demo" session. */
export default function DemoBanner() {
  return (
    <div role="note" className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-accent/40 bg-accent-soft px-6 py-2 text-sm lg:px-10">
      <Eye aria-hidden size={14} strokeWidth={1.75} className="text-accent" />
      <span className="font-medium text-accent">{DEMO_BANNER_TEXT}</span>
      <span className="text-ink-muted">
        Ask questions and propose changes on the demo repositories. Proposals are display-only; nothing is ever applied. Pre-generated examples answer instantly.
      </span>
    </div>
  );
}
