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
