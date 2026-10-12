import { argon2Sync } from 'node:crypto';

import { describe, expect, it } from 'vitest';

import {
  DEFAULT_PARAMS,
  decodeStoredHash,
  encodeStoredHash,
  formatPhc,
  hashPassword,
  parsePhc,
  verifyPassword,
} from '@/lib/crypto/argon2id';

const FAST = { memory: 8192, passes: 1, parallelism: 1, tagLength: 32 };

describe('runtime Argon2id implementation', () => {
  it('matches the RFC 9106 section 5.3 Argon2id test vector', () => {
    const tag = argon2Sync('argon2id', {
      message: Buffer.alloc(32, 0x01),
      nonce: Buffer.alloc(16, 0x02),
      secret: Buffer.alloc(8, 0x03),
      associatedData: Buffer.alloc(12, 0x04),
      parallelism: 4,
      tagLength: 32,
      memory: 32,
      passes: 3,
    });
    expect(tag.toString('hex')).toBe('0d640df58d78766c08c037a34a8b53c9d01ef0452d75b65eb52520e96b01e659');
  });
});

describe('hashPassword / verifyPassword', () => {
  it('produces a standard PHC string with the default parameters', async () => {
    const phc = await hashPassword('correct horse battery staple');
    expect(phc).toMatch(/^\$argon2id\$v=19\$m=65536,t=3,p=1\$[A-Za-z0-9+/]{22}\$[A-Za-z0-9+/]{43}$/);
    expect(parsePhc(phc).params).toEqual(DEFAULT_PARAMS);
  });

  it('accepts the right password and rejects a wrong one (base64url-wrapped and raw)', async () => {
    const phc = await hashPassword('correct horse battery staple', FAST);
    for (const stored of [encodeStoredHash(phc), phc]) {
      expect(await verifyPassword('correct horse battery staple', stored)).toBe(true);
      expect(await verifyPassword('correct horse battery stapl', stored)).toBe(false);
      expect(await verifyPassword('Correct horse battery staple', stored)).toBe(false);
    }
  });

  it('uses a fresh random salt for every hash', async () => {
    const [a, b] = await Promise.all([hashPassword('same', FAST), hashPassword('same', FAST)]);
    expect(a).not.toBe(b);
  });

  it('rejects empty, oversized, and non-string passwords without hashing them', async () => {
    const stored = encodeStoredHash(await hashPassword('x'.repeat(12), FAST));
    expect(await verifyPassword('', stored)).toBe(false);
    expect(await verifyPassword('x'.repeat(1025), stored)).toBe(false);
    expect(await verifyPassword(undefined, stored)).toBe(false);
  });

  it('wrapping survives Next.js $-expansion concerns: the env value contains no "$"', async () => {
    const stored = encodeStoredHash(await hashPassword('x'.repeat(12), FAST));
    expect(stored).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(decodeStoredHash(stored).startsWith('$argon2id$')).toBe(true);
  });
});

describe('stored-hash validation (fail closed on bad configuration)', () => {
  const salt = Buffer.alloc(16, 7);
  const hash = Buffer.alloc(32, 9);

  it.each([
    ['empty', ''],
    ['not base64url', 'not a hash!'],
    ['argon2i instead of argon2id', '$argon2i$v=19$m=65536,t=3,p=1$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'],
    ['old version', '$argon2id$v=16$m=65536,t=3,p=1$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'],
    ['memory too large (DoS)', formatPhc({ memory: 4194304, passes: 3, parallelism: 1 }, salt, hash)],
    ['too many passes (DoS)', formatPhc({ memory: 65536, passes: 99, parallelism: 1 }, salt, hash)],
    ['memory too small (weak)', formatPhc({ memory: 1024, passes: 3, parallelism: 1 }, salt, hash)],
    ['salt too short', formatPhc({ memory: 65536, passes: 3, parallelism: 1 }, Buffer.alloc(8), hash)],
  ])('throws invalid_stored_hash for: %s', async (_label, value) => {
    const stored = value.startsWith('$') ? encodeStoredHash(value) : value;
    await expect(verifyPassword('anything-long-enough', stored)).rejects.toThrow('invalid_stored_hash');
  });
});
