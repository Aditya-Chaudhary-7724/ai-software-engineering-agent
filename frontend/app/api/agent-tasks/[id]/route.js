import { handleGetAgentTask } from '@/lib/server/agent-task-routes';
import { withRouteErrorBoundary } from '@/lib/server/route-helpers';

export const GET = withRouteErrorBoundary(async (_request, { params }) => handleGetAgentTask((await params).id));
