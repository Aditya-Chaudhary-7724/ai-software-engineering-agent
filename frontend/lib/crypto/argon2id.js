/**
 * Argon2id password hashing via Node's built-in `crypto.argon2` (Node >= 24.7).
 *
 * Why built-in: no native addon to compile and no WASM bundle — one fewer
 * dependency on the most security-sensitive path in the frontend. The cost
 * is a Node >= 24.7 runtime requirement (enforced in package.json "engines"
 * and checked at runtime below). If a deployment target can't run Node 24,
 * `@node-rs/argon2` is the documented drop-in alternative.
 *
 * Storage format: a standard PHC string
 *   $argon2id$v=19$m=65536,t=3,p=1$<salt>$<hash>
 * wrapped in base64url for the environment variable, because Next.js expands
 * `$NAME` references inside .env files and would silently corrupt a raw PHC
 * string. `decodeStoredHash` also accepts a raw PHC string, for platforms that
 * set env vars without expansion.
 *
 * This module holds no secrets, so it is not marked `server-only`: the CLI
 * (`scripts/hash-password.mjs`) imports it too. It can never run in a browser
 * anyway — it depends on `node:crypto`.
 */

import { argon2, randomBytes, timingSafeEqual } from 'node:crypto';

// RFC 9106 section 4, second recommended option, with p=1 (one lane — a
// single operator logs in rarely, and p=1 keeps memory use predictable).
export const DEFAULT_PARAMS = Object.freeze({ memory: 65536, passes: 3, parallelism: 1, tagLength: 32 });
export const SALT_BYTES = 16;
export const MAX_PASSWORD_BYTES = 1024;

// Bounds on parameters READ FROM CONFIGURATION, so a malformed or hostile
// hash value cannot make a login attempt allocate gigabytes or spin for minutes.
const BOUNDS = { memoryMin: 8192, memoryMax: 262144, passesMax: 10, parallelismMax: 8, tagMin: 16, tagMax: 64 };

const PHC_PATTERN = /^\$argon2id\$v=19\$m=(\d{1,7}),t=(\d{1,2}),p=(\d{1,2})\$([A-Za-z0-9+/]{22,88})\$([A-Za-z0-9+/]{22,88})$/;

export function isArgon2Available() {
  return typeof argon2 === 'function';
}

function argon2idRaw(password, salt, params) {
  return new Promise((resolve, reject) => {
    argon2(
      'argon2id',
      {
        message: password,
        nonce: salt,
        parallelism: params.parallelism,
        tagLength: params.tagLength,
        memory: params.memory,
        passes: params.passes,
      },
      (error, derived) => (error ? reject(error) : resolve(derived)),
    );
  });
}

const toB64 = (buffer) => buffer.toString('base64').replace(/=+$/, '');
const fromB64 = (text) => Buffer.from(text, 'base64');

export function formatPhc(params, salt, hash) {
  return `$argon2id$v=19$m=${params.memory},t=${params.passes},p=${params.parallelism}$${toB64(salt)}$${toB64(hash)}`;
}

/** Returns `{ params, salt, hash }`, or `null` if the string is not an acceptable Argon2id PHC hash. */
export function parsePhc(phc) {
  const match = typeof phc === 'string' ? PHC_PATTERN.exec(phc) : null;
  if (!match) return null;
  const [memory, passes, parallelism] = match.slice(1, 4).map(Number);
  const salt = fromB64(match[4]);
  const hash = fromB64(match[5]);
  const valid =
    memory >= BOUNDS.memoryMin && memory <= BOUNDS.memoryMax && memory >= 8 * parallelism &&
    passes >= 1 && passes <= BOUNDS.passesMax &&
    parallelism >= 1 && parallelism <= BOUNDS.parallelismMax &&
    salt.length >= SALT_BYTES &&
    hash.length >= BOUNDS.tagMin && hash.length <= BOUNDS.tagMax;
  return valid ? { params: { memory, passes, parallelism, tagLength: hash.length }, salt, hash } : null;
}

export function encodeStoredHash(phc) {
  return Buffer.from(phc, 'utf8').toString('base64url');
}

export function decodeStoredHash(value) {
  if (typeof value !== 'string' || value.length === 0) return null;
  if (value.startsWith('$argon2id$')) return value;
  if (!/^[A-Za-z0-9_-]+$/.test(value)) return null;
  return Buffer.from(value, 'base64url').toString('utf8');
}

export async function hashPassword(password, params = DEFAULT_PARAMS) {
  const salt = randomBytes(SALT_BYTES);
  const hash = await argon2idRaw(Buffer.from(password, 'utf8'), salt, params);
  return formatPhc(params, salt, hash);
}

/**
 * Verifies `password` against a stored (base64url-wrapped or raw) PHC hash.
 * Throws `Error('invalid_stored_hash')` when the CONFIGURED hash is unusable —
 * that is a deployment error, distinct from a wrong password, and must not be
 * reported to the user as "invalid credentials".
 */
export async function verifyPassword(password, storedValue) {
  const parsed = parsePhc(decodeStoredHash(storedValue));
  if (!parsed) throw new Error('invalid_stored_hash');
  if (typeof password !== 'string') return false;
  const passwordBytes = Buffer.from(password, 'utf8');
  if (passwordBytes.length === 0 || passwordBytes.length > MAX_PASSWORD_BYTES) return false;

  const candidate = await argon2idRaw(passwordBytes, parsed.salt, parsed.params);
  return candidate.length === parsed.hash.length && timingSafeEqual(candidate, parsed.hash);
}
