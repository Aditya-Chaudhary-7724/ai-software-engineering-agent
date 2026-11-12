import { describe, expect, it } from 'vitest';

import { testOutcome } from '@/lib/agent-tasks';
import { sandboxCapability } from '@/lib/capabilities';

describe('testOutcome', () => {
  it('reports a disabled sandbox as not run, never as passed', () => {
    const result = testOutcome({ test_ran: false, test_passed: null, test_skipped_reason: 'sandbox_disabled' });
    expect(result).toEqual({ text: 'not run: sandbox disabled in this deployment', tone: 'warn' });
    expect(result.text).not.toMatch(/passed|verified/);
  });

  it('labels every skip reason as a warning', () => {
    for (const reason of ['sandbox_disabled', 'sandbox_unavailable', 'no_test_command', 'something_new']) {
      const result = testOutcome({ test_ran: false, test_skipped_reason: reason });
      expect(result.tone).toBe('warn');
      expect(result.text.startsWith('not run')).toBe(true);
    }
  });

  it('a skip reason never overrides a stale test_passed into a pass', () => {
    expect(testOutcome({ test_ran: false, test_passed: true, test_skipped_reason: 'sandbox_disabled' }).tone).toBe('warn');
  });

  it('only test_ran with test_passed === true reads as passed', () => {
    expect(testOutcome({ test_ran: true, test_passed: true })).toEqual({ text: 'passed', tone: 'ok' });
    expect(testOutcome({ test_ran: true, test_passed: false })).toEqual({ text: 'failed', tone: 'err' });
    expect(testOutcome({ test_ran: true, test_passed: null })).toEqual({ text: 'failed', tone: 'err' });
  });

  it('shows nothing when tests were never reached', () => {
    expect(testOutcome({ test_ran: false, test_passed: null })).toBeNull();
    expect(testOutcome(undefined)).toBeNull();
  });
});

describe('sandboxCapability', () => {
  it('reports a disabled sandbox even if Docker probes were somehow true', () => {
    expect(sandboxCapability({ sandbox_enabled: false, sandbox_docker_available: true, sandbox_image_available: true }))
      .toEqual(['idle', 'disabled in this deployment']);
  });

  it('keeps the Docker-based states when enabled', () => {
    expect(sandboxCapability({ sandbox_enabled: true, sandbox_docker_available: true, sandbox_image_available: true })).toEqual(['ok', 'ready']);
    expect(sandboxCapability({ sandbox_enabled: true, sandbox_docker_available: true, sandbox_image_available: false })).toEqual(['idle', 'image not built']);
    expect(sandboxCapability({ sandbox_enabled: true, sandbox_docker_available: false, sandbox_image_available: false })).toEqual(['idle', 'Docker unavailable']);
  });
});
