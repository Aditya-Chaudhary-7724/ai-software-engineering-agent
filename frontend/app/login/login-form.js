'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';

const MESSAGES = {
  invalid_credentials: 'Incorrect password.',
  invalid_request: 'The request was rejected. Reload the page and try again.',
  forbidden_origin: 'The request was rejected. Reload the page and try again.',
  auth_not_configured: 'Sign-in is not configured on this server.',
};

function messageFor(code, retryAfterSeconds) {
  if (code === 'too_many_attempts') {
    const minutes = Math.max(1, Math.ceil((retryAfterSeconds || 60) / 60));
    return `Too many attempts. Try again in about ${minutes} minute${minutes === 1 ? '' : 's'}.`;
  }
  return MESSAGES[code] ?? 'Sign-in failed. Try again.';
}

export default function LoginForm({ next }) {
  const router = useRouter();
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event) {
    event.preventDefault();
    if (pending || password.length === 0) return;
    setPending(true);
    setError(null);
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
        credentials: 'same-origin',
      });
      if (response.ok) {
        setPassword('');
        router.replace(next);
        router.refresh();
        return;
      }
      const body = await response.json().catch(() => ({}));
      setError(messageFor(body.error, Number(response.headers.get('retry-after'))));
    } catch {
      setError('Could not reach the server.');
    }
    setPending(false);
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div>
        <label htmlFor="password" className="label-micro mb-1.5 block">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          autoFocus
          required
          maxLength={1024}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          aria-invalid={error ? 'true' : undefined}
          aria-describedby={error ? 'login-error' : undefined}
          className="block w-full border border-rule-strong bg-surface px-3 py-2 font-mono text-sm outline-none focus:border-accent"
        />
      </div>
      {error ? (
        <p id="login-error" role="alert" className="border-l-2 border-err px-3 py-1.5 text-sm text-err">
          {error}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={pending || password.length === 0}
        className="w-full bg-ink px-3 py-2 text-sm font-medium text-paper transition-opacity disabled:opacity-40"
      >
        {pending ? 'Verifying…' : 'Sign in'}
      </button>
    </form>
  );
}
