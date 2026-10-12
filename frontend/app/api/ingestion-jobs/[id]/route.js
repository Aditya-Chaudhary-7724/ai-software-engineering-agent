import { handleGetJob } from '@/lib/server/job-routes';
import { withRouteErrorBoundary } from '@/lib/server/route-helpers';

export const GET = withRouteErrorBoundary(async (_request, { params }) => handleGetJob((await params).id));
