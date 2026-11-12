/**
 * Sandbox entry for the capability strip: [tone, detail]. "Disabled" is a
 * deployment decision (SANDBOX_ENABLED is off), reported as such rather
 * than as a fault. `sandbox_enabled` missing (an older backend) falls back
 * to the Docker probes.
 */
export function sandboxCapability(c) {
  if (c.sandbox_enabled === false) return ['idle', 'disabled in this deployment'];
  if (c.sandbox_image_available) return ['ok', 'ready'];
  return ['idle', c.sandbox_docker_available ? 'image not built' : 'Docker unavailable'];
}

/**
 * LLM entry for the capability strip: [tone, detail]. `llm_provider` missing
 * (an older backend) falls back to the boolean.
 */
export function llmCapability(c) {
  if (c.llm_provider === 'misconfigured') return ['err', 'misconfigured (see check_production_config.py)'];
  if (c.llm_provider === 'stub' || (!c.llm_provider && !c.llm_provider_configured)) return ['warn', 'stub (placeholder output)'];
  return ['ok', c.llm_provider ?? 'provider configured'];
}

/** Embeddings entry for the capability strip: [tone, detail]. */
export function embeddingCapability(c) {
  if (c.embedding_provider === 'misconfigured') return ['err', 'misconfigured (see check_production_config.py)'];
  if (c.embedding_provider === 'fastembed') return ['ok', 'local model (fastembed)'];
  if (c.embedding_provider === 'deterministic_local' || !c.embedding_provider_configured) return ['warn', 'hash stand-in, non-semantic'];
  return ['ok', c.embedding_provider ?? 'provider configured'];
}
