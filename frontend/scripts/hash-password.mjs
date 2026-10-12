#!/usr/bin/env node
/**
 * Generates OPERATOR_PASSWORD_HASH for the single operator login.
 *
 *   npm run hash-password                      # interactive, input hidden, asks twice
 *   printf '%s' "$PW" | npm run -s hash-password   # non-interactive (stdin)
 *
 * The password is never accepted as a command-line argument (it would land
 * in shell history and be visible to other users via `ps`), and is never
 * printed. Output is the base64url-wrapped Argon2id PHC string — see
 * lib/crypto/argon2id.js for why it's wrapped.
 */

import { stdin, stdout, stderr, exit } from 'node:process';

import { DEFAULT_PARAMS, MAX_PASSWORD_BYTES, encodeStoredHash, hashPassword, isArgon2Available, verifyPassword } from '../lib/crypto/argon2id.js';

const MIN_PASSWORD_LENGTH = 12;

function readHidden(prompt) {
  return new Promise((resolve, reject) => {
    stderr.write(prompt);
    let value = '';
    stdin.setRawMode(true);
    stdin.resume();
    stdin.setEncoding('utf8');
    const onData = (chunk) => {
      for (const char of chunk) {
        if (char === '\r' || char === '\n') {
          stdin.setRawMode(false);
          stdin.pause();
          stdin.off('data', onData);
          stderr.write('\n');
          return resolve(value);
        }
        if (char === '\u0003') {
          stdin.setRawMode(false);
          return reject(new Error('cancelled'));
        }
        if (char === '\u007f' || char === '\b') value = value.slice(0, -1);
        else value += char;
      }
    };
    stdin.on('data', onData);
  });
}

async function readPiped() {
  let data = '';
  for await (const chunk of stdin) data += chunk;
  return data.replace(/\r?\n$/, '');
}

async function main() {
  if (!isArgon2Available()) {
    stderr.write('This Node.js build has no crypto.argon2 (requires Node >= 24.7).\n');
    exit(1);
  }

  let password;
  if (stdin.isTTY) {
    password = await readHidden('Operator password: ');
    const confirmation = await readHidden('Confirm password: ');
    if (password !== confirmation) {
      stderr.write('Passwords do not match.\n');
      exit(1);
    }
  } else {
    password = await readPiped();
  }

  if ([...password].length < MIN_PASSWORD_LENGTH) {
    stderr.write(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.\n`);
    exit(1);
  }
  if (Buffer.byteLength(password, 'utf8') > MAX_PASSWORD_BYTES) {
    stderr.write(`Password must be at most ${MAX_PASSWORD_BYTES} bytes.\n`);
    exit(1);
  }

  const phc = await hashPassword(password);
  const stored = encodeStoredHash(phc);
  if (!(await verifyPassword(password, stored))) throw new Error('self-check failed');

  stderr.write(`Argon2id m=${DEFAULT_PARAMS.memory}KiB t=${DEFAULT_PARAMS.passes} p=${DEFAULT_PARAMS.parallelism}. Add this line to frontend/.env.local:\n`);
  stdout.write(`OPERATOR_PASSWORD_HASH=${stored}\n`);
}

main().catch((error) => {
  stderr.write(`hash-password failed: ${error.message}\n`);
  exit(1);
});
