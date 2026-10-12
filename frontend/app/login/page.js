import { redirect } from 'next/navigation';

import { ConfigError, getAuthConfig } from '@/lib/server/config';
import { getSession } from '@/lib/server/dal';
import { safeNextPath } from '@/lib/safe-redirect';

import LoginForm from './login-form';

export const metadata = { title: 'Sign in' };

function authConfigured() {
  try {
    getAuthConfig();
    return true;
  } catch (error) {
    if (error instanceof ConfigError) return false;
    throw error;
  }
}

export default async function LoginPage({ searchParams }) {
  if (await getSession()) redirect('/');
  const next = safeNextPath((await searchParams)?.next);
  const configured = authConfigured();

  return (
    <main className="grid min-h-screen grid-cols-1 md:grid-cols-[1fr_minmax(0,28rem)]">
      <section className="hidden border-r border-rule p-10 md:flex md:flex-col md:justify-between">
        <div>
          <p className="label-micro">AI Software Engineering Agent</p>
          <p className="mt-3 text-[2rem] font-semibold leading-tight tracking-[-0.02em]">Engineering Lab</p>
          <p className="mt-3 max-w-md text-sm text-ink-muted">
            Software intelligence workspace: repository structure, knowledge graph, agent traces, and — in later phases —
            reviewed, sandbox-tested code changes.
          </p>
        </div>
        <dl className="grid max-w-md grid-cols-[max-content_1fr] gap-x-6 gap-y-2 font-mono text-xs text-ink-muted">
          <dt className="text-ink-faint">access</dt>
          <dd>single operator</dd>
          <dt className="text-ink-faint">session</dt>
          <dd>HttpOnly · SameSite=Strict · signed</dd>
          <dt className="text-ink-faint">password</dt>
          <dd>Argon2id, verified server-side</dd>
        </dl>
      </section>

      <section className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <h1 className="text-xl font-semibold tracking-[-0.01em]">Operator sign-in</h1>
          <p className="mt-1 text-sm text-ink-muted">This workspace has a single operator account. There is no registration.</p>
          <div className="mt-6">
            {configured ? (
              <LoginForm next={next} />
            ) : (
              <div role="alert" className="border-l-2 border-warn bg-warn/5 px-4 py-3 text-sm">
                <p className="font-medium text-warn">Sign-in is not configured on this server</p>
                <p className="mt-1 text-ink-muted">The operator must set the server-side authentication variables. See frontend/README.md.</p>
              </div>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}
