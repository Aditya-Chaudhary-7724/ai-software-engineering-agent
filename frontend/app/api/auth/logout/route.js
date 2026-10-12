import { handleLogout } from '@/lib/server/auth-handlers';
import { withRouteErrorBoundary } from '@/lib/server/route-helpers';

export const POST = withRouteErrorBoundary((request) => handleLogout(request));
