import 'server-only';

import { requireAuthenticatedMutation } from './auth-handlers';
import { api } from './backend';
import { REPOSITORY_ID, errorResponse as error, fromBackend, readJsonObject } from './route-helpers';

/**
 * Route Handler logic for F6 agent tasks (kept here so tests can call it
 * directly). Same rules as F3/F4: every POST passes
 * `requireAuthenticatedMutation` (session + same origin + CSRF token), the
 * shapes are validated here, and the backend request is rebuilt from the
 * validated values only — unknown fields are never forwarded.
 *
 * The approval decision is validated STRICTLY: `approved` must be a JSON
 * boolean (`typeof === 'boolean'`, so "no", 1, {} and null are refused
 * before the backend is called), and both binding values must be present.
 * The backend re-validates all of it and is the authority.
 */

export const MAX_INSTRUCTION_CHARS = 2000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const INTERRUPT_ID = /^[0-9a-f]{1,128}$/;
const SHA256 = /^[0-9a-f]{64}$/;

async function readInstruction(request) {
  const body = await readJsonObject(request, 16_384);
  const instruction = typeof body?.instruction === 'string' ? body.instruction.trim() : '';
  return instruction && instruction.length <= MAX_INSTRUCTION_CHARS ? instruction : null;
}

export async function handleRoutePreview(request) {
  const guard = requireAuthenticatedMutation(request);
  if (guard.response) return guard.response;
  const instruction = await readInstruction(request);
  if (!instruction) return error(400, 'invalid_instruction');
  return fromBackend(await api.agentRoutePreview(instruction));
}

export async function handleSubmitAgentTask(request, repositoryId) {
  const guard = requireAuthenticatedMutation(request);
  if (guard.response) return guard.response;
  if (!REPOSITORY_ID.test(repositoryId ?? '')) return error(400, 'invalid_request');
  const instruction = await readInstruction(request);
  if (!instruction) return error(400, 'invalid_instruction');
  return fromBackend(await api.submitAgentTask(repositoryId, instruction));
}

export async function handleGetAgentTask(taskId) {
  if (!UUID.test(taskId ?? '')) return error(400, 'invalid_task_id');
  return fromBackend(await api.agentTask(taskId));
}

export async function handleAgentDecision(request, taskId) {
  const guard = requireAuthenticatedMutation(request);
  if (guard.response) return guard.response;
  if (!UUID.test(taskId ?? '')) return error(400, 'invalid_task_id');
  const body = await readJsonObject(request);
  if (
    !body ||
    typeof body.approved !== 'boolean' ||
    typeof body.interruptId !== 'string' ||
    !INTERRUPT_ID.test(body.interruptId) ||
    typeof body.diffSha256 !== 'string' ||
    !SHA256.test(body.diffSha256)
  ) {
    return error(400, 'invalid_decision');
  }
  return fromBackend(
    await api.decideAgentTask(taskId, { approved: body.approved, interruptId: body.interruptId, diffSha256: body.diffSha256 }),
  );
}
