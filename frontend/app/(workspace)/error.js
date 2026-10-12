'use client';

/** Last-resort boundary: never renders the error's message or stack. */
export default function WorkspaceError({ reset }) {
  return (
    <div role="alert" className="border-l-2 border-err px-4 py-3">
      <p className="text-sm font-medium text-err">Something went wrong rendering this page.</p>
      <p className="mt-1 text-sm text-ink-muted">Details were written to the server log.</p>
      <button type="button" onClick={reset} className="mt-3 text-sm text-accent hover:underline">Try again</button>
    </div>
  );
}
