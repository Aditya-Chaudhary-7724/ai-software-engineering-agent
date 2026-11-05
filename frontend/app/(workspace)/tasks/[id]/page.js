import Link from 'next/link';
import { notFound } from 'next/navigation';

import AgentTaskView from '@/components/agent-task-view';
import { ErrorNotice, PageHeader } from '@/components/ui';
import { api } from '@/lib/server/backend';
import { getCsrfToken } from '@/lib/server/dal';

export const metadata = { title: 'Agent task' };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export default async function TaskPage({ params }) {
  const { id } = await params;
  if (!UUID.test(id)) notFound();
  const [result, csrfToken] = await Promise.all([api.agentTask(id), getCsrfToken()]);
  if (!result.ok && result.error === 'agent_task_not_found') notFound();
  const repository = result.ok ? await api.repository(result.data.repository_id) : null;
  const repositoryName = repository?.ok ? repository.data.github_full_name ?? repository.data.name : undefined;

  return (
    <div className="space-y-6">
      <PageHeader eyebrow={<Link href="/tasks" className="hover:text-accent">Agent tasks</Link>} title="Agent task" meta={id} />
      {!result.ok ? (
        <ErrorNotice code={result.error} />
      ) : (
        <AgentTaskView key={result.data.id} initialTask={result.data} csrfToken={csrfToken} repositoryName={repositoryName} />
      )}
    </div>
  );
}
