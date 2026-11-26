import Link from 'next/link';

import AskPanel from '@/components/ask-panel';
import DemoExamples from '@/components/demo-examples';
import { EmptyState, ErrorNotice, PageHeader } from '@/components/ui';
import { api } from '@/lib/server/backend';
import { getCsrfToken, getSession, isDemoSession } from '@/lib/server/dal';

// Demo repositories are few (DEMO_REPOSITORY_IDS); this bounds the reads anyway.
const MAX_EXAMPLE_REPOSITORIES = 5;

export const metadata = { title: 'Ask' };

export default async function AskPage({ searchParams }) {
  const requested = (await searchParams)?.repository;
  const [repositories, csrfToken] = await Promise.all([api.repositories(200, 0), getCsrfToken()]);
  const usable = repositories.ok ? repositories.data.repositories.filter((r) => r.chunk_count > 0) : [];
  const initialRepositoryId = usable.some((r) => String(r.id) === requested) ? requested : undefined;
  const demo = isDemoSession(await getSession());
  const exampleRepos = demo ? usable.slice(0, MAX_EXAMPLE_REPOSITORIES) : [];
  const examples = await Promise.all(exampleRepos.map((r) => api.demoExamples(r.id)));

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Workspace"
        title="Ask"
        description="Read-only questions about an indexed repository. Answers come from hybrid retrieval (vector + keyword + knowledge graph when available) and cite the exact code they were given."
      />
      {!repositories.ok ? (
        <ErrorNotice code={repositories.error} />
      ) : usable.length === 0 ? (
        <EmptyState title="No indexed code to ask about yet">
          Ask needs a repository with at least one indexed code chunk. <Link href="/repositories" className="text-accent hover:underline">Add a repository</Link> first.
        </EmptyState>
      ) : (
        <AskPanel repositories={usable} initialRepositoryId={initialRepositoryId} csrfToken={csrfToken} />
      )}
      {exampleRepos.map((repository, i) => <DemoExamples key={repository.id} repository={repository} result={examples[i]} show="ask" />)}
    </div>
  );
}
