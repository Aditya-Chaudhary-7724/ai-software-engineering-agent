'use client';

import { Boxes, FlaskConical, GitPullRequestArrow, LayoutDashboard, MessageSquareText, Network, Route } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

const AVAILABLE = [
  { href: '/', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/repositories', label: 'Repositories', icon: Boxes },
  { href: '/ask', label: 'Ask', icon: MessageSquareText },
  { href: '/graph', label: 'Knowledge graph', icon: Network },
  { href: '/traces', label: 'Agent traces', icon: Route },
];

// Shown so the workspace's scope is honest, but not linked: these screens
// need backend endpoints that later phases add.
const PLANNED = [
  { label: 'Changes & approval', phase: 'F6', icon: GitPullRequestArrow },
  { label: 'Tests & evaluation', phase: 'F7', icon: FlaskConical },
];

function isActive(pathname, href) {
  return href === '/' ? pathname === '/' : pathname === href || pathname.startsWith(`${href}/`);
}

export default function WorkspaceNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Workspace" className="space-y-6">
      <ul className="space-y-px">
        {AVAILABLE.map(({ href, label, icon: Icon }) => {
          const active = isActive(pathname, href);
          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={active ? 'page' : undefined}
                className={`flex items-center gap-2.5 border-l-2 px-3 py-1.5 text-sm transition-colors ${
                  active ? 'border-accent bg-sunken font-medium text-ink' : 'border-transparent text-ink-muted hover:bg-sunken hover:text-ink'
                }`}
              >
                <Icon aria-hidden size={15} strokeWidth={1.75} />
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
      <div>
        <p className="label-micro mb-1.5 px-3">Later phases</p>
        <ul className="space-y-px">
          {PLANNED.map(({ label, phase, icon: Icon }) => (
            <li key={label} aria-disabled="true" className="flex items-center gap-2.5 border-l-2 border-transparent px-3 py-1.5 text-sm text-ink-faint">
              <Icon aria-hidden size={15} strokeWidth={1.75} />
              <span className="flex-1">{label}</span>
              <span className="font-mono text-[0.625rem]">{phase}</span>
            </li>
          ))}
        </ul>
      </div>
    </nav>
  );
}
