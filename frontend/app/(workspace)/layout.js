import CapabilityStrip from '@/components/capability-strip';
import LogoutButton from '@/components/logout-button';
import WorkspaceNav from '@/components/workspace-nav';
import { formatTimestamp } from '@/lib/format';
import { api } from '@/lib/server/backend';
import { getCsrfToken, requireSession } from '@/lib/server/dal';

/**
 * Protected shell. `requireSession()` here is a second gate after proxy.js —
 * but layouts are NOT re-rendered on client-side navigation between child
 * pages, so the per-request guarantee comes from every page's data access
 * (`api.*` -> `backendGet` -> `requireSession()`), not from this layout alone.
 */
export default async function WorkspaceLayout({ children }) {
  const session = await requireSession();
  const [csrfToken, capabilities] = await Promise.all([getCsrfToken(), api.capabilities()]);

  return (
    <div className="grid min-h-screen grid-cols-[15rem_minmax(0,1fr)]">
      <aside className="sticky top-0 flex h-screen flex-col border-r border-rule bg-surface">
        <div className="border-b border-rule px-4 py-4">
          <p className="label-micro">ASE Agent</p>
          <p className="mt-0.5 text-[0.9375rem] font-semibold tracking-[-0.01em]">Engineering Lab</p>
        </div>
        <div className="flex-1 overflow-y-auto py-4">
          <WorkspaceNav />
        </div>
        <div className="border-t border-rule py-3">
          <p className="px-3 pb-2 font-mono text-[0.6875rem] text-ink-faint">session until {formatTimestamp(session.exp * 1000)}</p>
          <LogoutButton csrfToken={csrfToken} />
        </div>
      </aside>
      <div className="min-w-0">
        <CapabilityStrip result={capabilities} />
        <main className="px-6 py-6 lg:px-10">{children}</main>
      </div>
    </div>
  );
}
