import Link from 'next/link';

import AgentTaskForm from '@/components/agent-task-form';
import { EmptyState, ErrorNotice, PageHeader, Panel, StatusDot } from '@/components/ui';
import { statusLabel, statusTone } from '@/lib/agent-tasks';
import { formatTimestamp } from '@/lib/format';
import { api } from '@/lib/server/backend';
import { getCsrfToken } from '@/lib/server/dal';

export const metadata = { title: 'Agent tasks' };

export default async function TasksPage({ searchParams }) {
  const requested = (await searchParams)?.repository;
  const [repositories, tasks, capabilities, csrfToken] = await Promise.all([
    api.repositories(200, 0),
    api.agentTasks({ limit: 25 }),
    api.capabilities(),
    getCsrfToken(),
  ]);
  const usable = repositories.ok ? repositories.data.repositories.filter((r) => r.chunk_count > 0 && r.available_on_disk) : [];
  const names = new Map((repositories.ok ? repositories.data.repositories : []).map((r) => [r.id, r.github_full_name ?? r.name]));
  const workerRunning = capabilities.ok && capabilities.data.features?.agent_execution === true;
  const initialRepositoryId = usable.some((r) => String(r.id) === requested) ? requested : undefined;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Workspace"
        title="Agent tasks"
        description="Give the agent a task. It routes the request (answer or modify), runs in the background, and for a code change shows you the exact proposed diff. Nothing is written until you approve that diff — and nothing is ever committed or pushed. For read-only questions, use Ask."
      />
      {!repositories.ok ? (
        <ErrorNotice code={repositories.error} />
      ) : usable.length === 0 ? (
        <EmptyState title="No repository is ready for agent tasks">
          A task needs an indexed repository whose files are on the server. <Link href="/repositories" className="text-accent hover:underline">Add a repository</Link> first.
        </EmptyState>
      ) : (
        <>
          {!workerRunning ? (
            <p role="status" className="border-l-2 border-warn bg-warn/5 px-4 py-2 text-sm text-warn">
              The backend’s agent worker is not running, so tasks cannot be submitted or decided right now.
            </p>
          ) : null}
          <AgentTaskForm repositories={usable} csrfToken={csrfToken} initialRepositoryId={initialRepositoryId} />
        </>
      )}

      <Panel title="Recent tasks" meta={tasks.ok ? `${tasks.data.tasks.length}` : undefined}>
        {!tasks.ok ? (
          <div className="p-4"><ErrorNotice code={tasks.error} compact /></div>
        ) : tasks.data.tasks.length === 0 ? (
          <EmptyState title="No tasks yet" />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-rule text-left">
                <th className="label-micro px-4 py-2 font-normal">Status</th>
                <th className="label-micro px-4 py-2 font-normal">Instruction</th>
                <th className="label-micro hidden px-4 py-2 font-normal md:table-cell">Route</th>
                <th className="label-micro hidden px-4 py-2 font-normal md:table-cell">Repository</th>
                <th className="label-micro hidden px-4 py-2 font-normal lg:table-cell">Submitted</th>
              </tr>
            </thead>
            <tbody>
              {tasks.data.tasks.map((task) => (
                <tr key={task.id} className="border-b border-rule last:border-0 hover:bg-sunken">
                  <td className="whitespace-nowrap px-4 py-2"><StatusDot tone={statusTone(task.status)} label={statusLabel(task.status)} /></td>
                  <td className="max-w-md px-4 py-2">
                    <Link href={`/tasks/${task.id}`} className="block truncate text-accent hover:underline">{task.instruction}</Link>
                  </td>
                  <td className="hidden px-4 py-2 font-mono text-xs md:table-cell">{task.route}</td>
                  <td className="hidden px-4 py-2 text-xs md:table-cell">{names.get(task.repository_id) ?? `#${task.repository_id}`}</td>
                  <td className="hidden whitespace-nowrap px-4 py-2 font-mono text-xs text-ink-muted lg:table-cell">{formatTimestamp(task.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
