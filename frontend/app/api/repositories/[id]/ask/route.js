import { handleAsk } from '@/lib/server/ask-route';
import { withRouteErrorBoundary } from '@/lib/server/route-helpers';

export const POST = withRouteErrorBoundary(async (request, { params }) => handleAsk(request, (await params).id));
