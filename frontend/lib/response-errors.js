/**
 * Maps any HTTP response from THIS app's Route Handlers to one stable error
 * code for the UI. Only a code the server actually sent is trusted (and
 * only if it looks like a code); everything else is classified by status —
 * never blamed on the backend, which may not have been involved at all.
 */
const CODE = /^[a-z_]{1,64}$/;

export function errorCodeFromResponse(status, body) {
  if (body && typeof body.error === 'string' && CODE.test(body.error)) return body.error;
  if (status >= 500) return 'frontend_server_error';
  return 'unexpected_response';
}

export const SERVER_SIDE_MESSAGES = {
  frontend_route_error: 'The web app’s server failed while handling this request. Details are in the Next.js server log.',
  frontend_server_error: 'The web app’s server returned an error without details. Check the Next.js server log.',
  unexpected_response: 'The server returned an unexpected response.',
};
