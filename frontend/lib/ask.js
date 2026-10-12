/**
 * Pure helpers for Ask (F4): citation links into the file viewer and the
 * honest retrieval/generation notices shown with every answer.
 */

import { SERVER_SIDE_MESSAGES } from './response-errors';

/** Link to the cited file, highlighting and scrolling to the cited lines. */
export function citationHref(repositoryId, source) {
  const params = new URLSearchParams({ path: source.relative_path, lines: `${source.start_line}-${source.end_line}` });
  return `/repositories/${repositoryId}?${params.toString()}#L${source.start_line}`;
}

/** Parses `?lines=12-30` (or `12`) into `{ start, end }`, or null. Bounded and validated. */
export function parseLineRange(value) {
  if (typeof value !== 'string' || !/^\d{1,7}(-\d{1,7})?$/.test(value)) return null;
  const [start, end = start] = value.split('-').map(Number);
  if (start < 1 || end < start || end - start > 100_000) return null;
  return { start, end };
}

const FOUND_VIA = {
  vector: 'vector',
  keyword: 'keyword',
};

export function foundViaLabel(value) {
  if (FOUND_VIA[value]) return FOUND_VIA[value];
  if (typeof value === 'string' && value.startsWith('graph:')) return `graph · ${value.slice(6).replace(/_/g, ' ')}`;
  return String(value);
}

/**
 * Notices that must accompany an answer. Every one is derived from fields
 * the backend reported — never assumed.
 */
export function answerNotices(response) {
  const notices = [];
  if (response.answer.is_placeholder) {
    notices.push({
      tone: 'warn',
      title: 'Placeholder answer',
      text: 'No LLM is configured (LLM_API_KEY), so the text below is a fixed stub message, not an answer. The cited sources are real retrieval results.',
    });
  }
  if (!response.retrieval.semantic_embeddings) {
    notices.push({
      tone: 'info',
      title: 'Non-semantic embeddings',
      text: 'This index uses local hash-based embeddings: "vector" matches do not reflect meaning. "keyword" matches are real full-text hits.',
    });
  }
  if (response.retrieval.graph === 'not_configured') {
    notices.push({ tone: 'info', title: 'No graph evidence', text: 'Neo4j is not configured, so retrieval used vector and keyword search only.' });
  } else if (response.retrieval.graph === 'unavailable') {
    notices.push({ tone: 'info', title: 'No graph evidence', text: 'Neo4j was unreachable, so retrieval used vector and keyword search only.' });
  } else if (response.retrieval.graph === 'used' && response.retrieval.graph_built === false) {
    notices.push({ tone: 'info', title: 'No graph for this repository', text: 'No knowledge graph has been built for it, so graph expansion found nothing.' });
  }
  if (response.warnings.includes('embedding_provider_mismatch')) {
    notices.push({
      tone: 'warn',
      title: 'Embedding mismatch',
      text: 'This repository was indexed with a different embedding provider than the one answering now; vector matches are unreliable until it is re-indexed.',
    });
  }
  if (response.warnings.includes('no_relevant_context')) {
    notices.push({ tone: 'info', title: 'No relevant code found', text: 'Retrieval returned no indexed code for this question, so nothing could be cited.' });
  }
  return notices;
}

const ASK_ERRORS = {
  ...SERVER_SIDE_MESSAGES,
  invalid_question: 'Enter a question (up to 2,000 characters).',
  repository_not_found: 'That repository no longer exists.',
  answer_generation_failed: 'The answer could not be generated (the LLM or embedding provider failed). Details are in the backend log.',
  database_unavailable: 'The database is unavailable.',
  backend_unreachable: 'The backend is not reachable.',
  csrf_token_invalid: 'The request could not be verified. Reload the page and try again.',
  forbidden_origin: 'The request could not be verified. Reload the page and try again.',
  unauthenticated: 'Your session has expired. Sign in again.',
};

export function askErrorMessage(code) {
  return ASK_ERRORS[code] ?? 'The question could not be answered.';
}
