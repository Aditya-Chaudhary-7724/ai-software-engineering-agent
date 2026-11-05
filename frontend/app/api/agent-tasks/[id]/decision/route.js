import { handleAgentDecision } from '@/lib/server/agent-task-routes';
import { withRouteErrorBoundary } from '@/lib/server/route-helpers';

export const POST = withRouteErrorBoundary(async (request, { params }) => handleAgentDecision(request, (await params).id));
