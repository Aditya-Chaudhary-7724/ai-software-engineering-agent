import { handleSubmitAgentTask } from '@/lib/server/agent-task-routes';
import { withRouteErrorBoundary } from '@/lib/server/route-helpers';

export const POST = withRouteErrorBoundary(async (request, { params }) => handleSubmitAgentTask(request, (await params).id));
