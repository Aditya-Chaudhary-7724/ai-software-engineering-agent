import { describe, expect, it } from 'vitest';

import {
  HASH_UNAVAILABLE,
  approvalGate,
  changeSummary,
  decisionBody,
  diffStats,
  formatRemaining,
  parseUnifiedDiff,
  sha256Hex,
  shouldPoll,
} from '@/lib/agent-tasks';

const NOW = Date.parse('2026-10-13T12:00:00Z');
const DIFF = '--- a/main.py\n+++ b/main.py\n@@ -1,2 +1,2 @@\n def greet():\n-    return 1\n+    return 2\n';
// Python: hashlib.sha256(DIFF.encode("utf-8")).hexdigest() — same algorithm
// as agent/approval.py::diff_sha256, so browser and server hashes agree.
const DIFF_HASH = await sha256Hex(DIFF);

function task(overrides = {}, proposal = {}) {
  return {
    id: 't',
    status: 'awaiting_approval',
    route: 'modify',
    decided_at: null,
    outcome: { changes_applied: 0, approved: null },
    proposal: {
      state: 'pending',
      interrupt_id: 'b'.repeat(32),
      diff: DIFF,
      diff_sha256: DIFF_HASH,
      expires_at: new Date(NOW + 30 * 60_000).toISOString(),
      ...proposal,
    },
    ...overrides,
  };
}

describe('sha256Hex', () => {
  it('matches a known SHA-256 vector', async () => {
    expect(await sha256Hex('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  });

  it('agrees with the backend: values from agent/approval.py::diff_sha256 (Python)', async () => {
    expect(DIFF_HASH).toBe('c43554bf427bbaae59faf3fb6d8b92418ca6a80970e38bde3aec050fc51e7949');
    // Non-ASCII content must hash its UTF-8 bytes on both sides.
    expect(await sha256Hex('+ héllo — 世界 🚀\n')).toBe('3d785ca99c0393501b8bf39dc240c0fc688132c6f2777f0371febed25b1f4c13');
  });
});

describe('approvalGate', () => {
  it('enables only a pending, unexpired proposal whose displayed diff hash matches', () => {
    expect(approvalGate(task(), { now: NOW, localHash: DIFF_HASH })).toEqual({ enabled: true, reason: null });
  });

  it.each([
    ['not awaiting', task({ status: 'running' })],
    ['completed', task({ status: 'completed' })],
    ['decided proposal', task({}, { state: 'approved' })],
    ['no interrupt id', task({}, { interrupt_id: null })],
    ['expired', task({}, { expires_at: new Date(NOW - 1).toISOString() })],
    ['no expiry', task({}, { expires_at: null })],
    ['no proposal', { ...task(), proposal: null }],
  ])('disables when %s', (_label, value) => {
    expect(approvalGate(value, { now: NOW, localHash: DIFF_HASH }).enabled).toBe(false);
  });

  it('disables while hashing, on a hash mismatch (stale diff), and while sending', () => {
    expect(approvalGate(task(), { now: NOW, localHash: null }).enabled).toBe(false);
    const mismatch = approvalGate(task(), { now: NOW, localHash: 'f'.repeat(64) });
    expect(mismatch.enabled).toBe(false);
    expect(mismatch.reason).toMatch(/does not match/);
    expect(approvalGate(task(), { now: NOW, localHash: DIFF_HASH, pending: true }).enabled).toBe(false);
  });

  // Audit fix: no Web Crypto (insecure context) is not reported as tampering.
  it('explains a missing Web Crypto instead of claiming a hash mismatch', () => {
    const gate = approvalGate(task(), { now: NOW, localHash: HASH_UNAVAILABLE });
    expect(gate.enabled).toBe(false);
    expect(gate.reason).toMatch(/Web Crypto needs HTTPS or localhost/);
    expect(gate.reason).not.toMatch(/does not match/);
  });
});

describe('decisionBody', () => {
  it('sends booleans only, with the locally computed hash', () => {
    expect(decisionBody(task(), true, DIFF_HASH)).toEqual({ approved: true, interruptId: 'b'.repeat(32), diffSha256: DIFF_HASH });
    expect(decisionBody(task(), false, DIFF_HASH).approved).toBe(false);
    for (const value of ['yes', 1, null, undefined, {}]) expect(() => decisionBody(task(), value, DIFF_HASH)).toThrow(TypeError);
  });
});

describe('changeSummary distinguishes proposed from applied', () => {
  it('awaiting approval: proposed, NOT applied', () => {
    const summary = changeSummary(task());
    expect(summary.title).toMatch(/NOT applied/);
    expect(summary.text).toMatch(/Nothing has been written/);
  });

  it('completed with an applied change: applied, never committed or pushed', () => {
    const summary = changeSummary(task({ status: 'completed', outcome: { changes_applied: 1, approved: true } }));
    expect(summary.title).toBe('Applied');
    expect(summary.text).toMatch(/not committed or pushed|Nothing was committed or pushed/);
  });

  it('approved but not applied (stale file) is not reported as applied', () => {
    expect(changeSummary(task({ status: 'completed', outcome: { changes_applied: 0, approved: true } })).title).toBe('Approved, but not applied');
  });

  it('rejected: nothing written, unless an earlier approved change was', () => {
    expect(changeSummary(task({ status: 'rejected', outcome: { changes_applied: 0 } })).text).toMatch(/Nothing was written/);
    expect(changeSummary(task({ status: 'rejected', outcome: { changes_applied: 1 } })).text).toMatch(/remains applied/);
  });

  it('expired, interrupted and failed states', () => {
    expect(changeSummary(task({ status: 'expired' })).title).toBe('Approval expired');
    expect(changeSummary(task({ status: 'interrupted', decided_at: 'x' })).text).toMatch(/may already be in the file/);
    expect(changeSummary(task({ status: 'interrupted' })).text).toMatch(/No approval had been given/);
    expect(changeSummary(task({ status: 'failed', error_code: 'approval_not_pending' })).tone).toBe('err');
  });

  // Audit fix: a task can fail after an approved change was already written.
  it('failed after an approval warns the change may be in the file', () => {
    const failed = changeSummary(task({ status: 'failed', error_code: 'internal_error' }, { state: 'approved' }));
    expect(failed.tone).toBe('err');
    expect(failed.text).toMatch(/may already be in the file/);
    expect(changeSummary(task({ status: 'failed', error_code: 'internal_error', outcome: { changes_applied: 1 } }, { state: 'rejected' })).text)
      .toMatch(/1 approved change was written earlier/);
    expect(changeSummary(task({ status: 'failed', error_code: 'internal_error' }, { state: 'rejected' })).text)
      .not.toMatch(/may already be in the file|written/);
    expect(changeSummary(task({ status: 'failed', error_code: 'internal_error', proposal: null })).text)
      .not.toMatch(/may already be in the file/);
  });
});

describe('parseUnifiedDiff', () => {
  it('classifies lines and tracks line numbers', () => {
    const lines = parseUnifiedDiff(DIFF);
    expect(lines.map((l) => l.kind)).toEqual(['meta', 'meta', 'hunk', 'context', 'del', 'add']);
    expect(lines[3]).toMatchObject({ oldNo: 1, newNo: 1 });
    expect(lines[4]).toMatchObject({ oldNo: 2, newNo: null });
    expect(lines[5]).toMatchObject({ oldNo: null, newNo: 2 });
    expect(diffStats(lines)).toEqual({ added: 1, removed: 1 });
  });

  it('keeps hostile content as plain text', () => {
    const [line] = parseUnifiedDiff('+<img src=x onerror=alert(1)>');
    expect(line).toMatchObject({ kind: 'add', text: '+<img src=x onerror=alert(1)>' });
  });

  it('tolerates non-strings', () => {
    expect(parseUnifiedDiff(null)).toEqual([]);
  });
});

describe('polling and timing', () => {
  it('polls only while queued or running', () => {
    expect(shouldPoll({ status: 'queued' })).toBe(true);
    expect(shouldPoll({ status: 'running' })).toBe(true);
    for (const status of ['awaiting_approval', 'completed', 'rejected', 'failed', 'expired', 'interrupted']) {
      expect(shouldPoll({ status })).toBe(false);
    }
  });

  it('formats the remaining approval window', () => {
    expect(formatRemaining(new Date(NOW + 90 * 60_000).toISOString(), NOW)).toBe('1h 30m left');
    expect(formatRemaining(new Date(NOW + 5 * 60_000).toISOString(), NOW)).toBe('5m left');
    expect(formatRemaining(new Date(NOW - 1).toISOString(), NOW)).toBe('expired');
  });
});
