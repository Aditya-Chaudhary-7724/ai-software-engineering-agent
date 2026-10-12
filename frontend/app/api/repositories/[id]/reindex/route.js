import { handleReindex } from '@/lib/server/job-routes';
import { withRouteErrorBoundary } from '@/lib/server/route-helpers';

export const POST = withRouteErrorBoundary(async (request, { params }) => handleReindex(request, (await params).id));
