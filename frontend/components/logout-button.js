'use client';

import { LogOut } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

/**
 * Logout is a state-changing POST, so it carries the session-bound CSRF
 * token (rendered into this page by the server) in the x-csrf-token header.
 */
export default function LogoutButton({ csrfToken }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function logout() {
    setPending(true);
    try {
      await fetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'x-csrf-token': csrfToken },
        credentials: 'same-origin',
      });
    } finally {
      router.replace('/login');
      router.refresh();
    }
  }

  return (
    <button
      type="button"
      onClick={logout}
      disabled={pending}
      className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-ink-muted transition-colors hover:bg-sunken hover:text-ink disabled:opacity-50"
    >
      <LogOut aria-hidden size={15} strokeWidth={1.75} />
      {pending ? 'Signing out…' : 'Sign out'}
    </button>
  );
}
