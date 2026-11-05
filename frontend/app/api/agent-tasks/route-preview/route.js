import { handleRoutePreview } from '@/lib/server/agent-task-routes';
import { withRouteErrorBoundary } from '@/lib/server/route-helpers';

export const POST = withRouteErrorBoundary((request) => handleRoutePreview(request));
