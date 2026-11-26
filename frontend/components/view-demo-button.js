'use client';

import { Eye } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { demoLimitMessage } from '@/lib/demo';

/** "View demo": no password. The server issues a short demo session. */
export default function ViewDemoButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);

  async function start() {
    setPending(true);
    setError(null);
    try {
      const response = await fetch('/api/auth/demo', { method: 'POST', credentials: 'same-origin' });
      if (response.ok) {
        router.replace('/');
        router.refresh();
        return;
      }
      const body = await response.json().catch(() => ({}));
      setError(demoLimitMessage(body.error) ?? 'The demo could not be started. Try again.');
    } catch {
      setError('The server could not be reached. Try again.');
    }
    setPending(false);
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={start}
        disabled={pending}
        className="inline-flex w-full items-center justify-center gap-2 bg-accent px-4 py-2 text-sm font-medium text-paper hover:opacity-90 disabled:opacity-50"
      >
        <Eye aria-hidden size={15} strokeWidth={1.75} />
        {pending ? 'Opening the demo…' : 'View demo'}
      </button>
      <p className="text-xs text-ink-muted">No sign-up. Read-only, with daily limits on live AI requests.</p>
      {error ? <p role="alert" className="text-sm text-err">{error}</p> : null}
    </div>
  );
}
