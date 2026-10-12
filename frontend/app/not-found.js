import Link from 'next/link';

export default function NotFound() {
  return (
    <main className="mx-auto max-w-md px-6 py-24">
      <p className="label-micro">404</p>
      <h1 className="mt-2 text-xl font-semibold">Not found</h1>
      <p className="mt-2 text-sm text-ink-muted">This page or record does not exist.</p>
      <Link href="/" className="mt-6 inline-block text-sm text-accent hover:underline">Back to the dashboard</Link>
    </main>
  );
}
