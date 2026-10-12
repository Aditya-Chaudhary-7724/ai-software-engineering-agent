import 'server-only';

/**
 * In-memory login limiter: a sliding window of FAILED attempts per client
 * key, plus a global window across all clients (a distributed guesser
 * rotating IPs still hits the global cap), plus a cap on concurrent Argon2
 * verifications (each allocates ~64 MiB).
 *
 * Checked BEFORE the password hash is computed, so a blocked client costs
 * the server nothing.
 *
 * Known limitation (documented, not hidden): state lives in one process.
 * Multiple Next.js instances each keep their own counters, and a restart
 * resets them. Sufficient for a single-operator deployment; a multi-instance
 * deployment would need a shared store (e.g. a Postgres table).
 */

export function createLoginRateLimiter({
  perClientFailures = 5,
  globalFailures = 30,
  windowMs = 15 * 60 * 1000,
  maxConcurrent = 4,
  maxTrackedClients = 10_000,
  now = () => Date.now(),
} = {}) {
  const failuresByClient = new Map();
  let globalFailureTimes = [];
  let inFlight = 0;

  const prune = (times) => times.filter((t) => now() - t < windowMs);
  const retryAfter = (times) => Math.max(1, Math.ceil((times[0] + windowMs - now()) / 1000));

  return {
    check(clientKey) {
      globalFailureTimes = prune(globalFailureTimes);
      const clientTimes = prune(failuresByClient.get(clientKey) ?? []);
      if (clientTimes.length >= perClientFailures) return { allowed: false, retryAfterSeconds: retryAfter(clientTimes) };
      if (globalFailureTimes.length >= globalFailures) return { allowed: false, retryAfterSeconds: retryAfter(globalFailureTimes) };
      return { allowed: true, retryAfterSeconds: 0 };
    },
    recordFailure(clientKey) {
      const times = prune(failuresByClient.get(clientKey) ?? []);
      times.push(now());
      failuresByClient.delete(clientKey); // re-insert to keep Map order = recency
      failuresByClient.set(clientKey, times);
      if (failuresByClient.size > maxTrackedClients) failuresByClient.delete(failuresByClient.keys().next().value);
      globalFailureTimes.push(now());
    },
    recordSuccess(clientKey) {
      failuresByClient.delete(clientKey);
    },
    tryAcquire() {
      if (inFlight >= maxConcurrent) return false;
      inFlight += 1;
      return true;
    },
    release() {
      inFlight = Math.max(0, inFlight - 1);
    },
  };
}

// One limiter per server process; stored on globalThis so dev-mode module
// reloading doesn't silently reset it.
export function getLoginRateLimiter() {
  globalThis.__aseaLoginRateLimiter ??= createLoginRateLimiter();
  return globalThis.__aseaLoginRateLimiter;
}
