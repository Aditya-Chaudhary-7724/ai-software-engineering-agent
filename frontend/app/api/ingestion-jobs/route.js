import { handleSubmitIngestion } from '@/lib/server/job-routes';
import { withRouteErrorBoundary } from '@/lib/server/route-helpers';

export const POST = withRouteErrorBoundary((request) => handleSubmitIngestion(request));
