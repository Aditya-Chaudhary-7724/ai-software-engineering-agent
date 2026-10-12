import 'server-only';

import { requireAuthenticatedMutation } from './auth-handlers';
import { api } from './backend';
import { REPOSITORY_ID, errorResponse, fromBackend, readJsonObject } from './route-helpers';

/**
 * POST /api/repositories/:id/ask (F4). Ask changes no state, but it is
 * still guarded like a mutation (session + same origin + CSRF token): it
 * can spend LLM/embedding credits, so a cross-site page must not be able
 * to trigger it. Only the validated question is forwarded.
 */
export const MAX_QUESTION_CHARS = 2000;

export async function handleAsk(request, repositoryId) {
  const guard = requireAuthenticatedMutation(request);
  if (guard.response) return guard.response;
  if (!REPOSITORY_ID.test(repositoryId ?? '')) return errorResponse(400, 'invalid_request');
  const body = await readJsonObject(request, 16_384);
  const question = typeof body?.question === 'string' ? body.question.trim() : '';
  if (!question || question.length > MAX_QUESTION_CHARS) return errorResponse(400, 'invalid_question');
  return fromBackend(await api.ask(repositoryId, question));
}
