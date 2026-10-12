import { describe, expect, it } from 'vitest';

import {
  MAX_POLL_FAILURES,
  graphOutcome,
  isTerminal,
  jobErrorMessage,
  nextPollDelay,
  stageStates,
  stagesFor,
  validateGithubUrl,
} from '@/lib/jobs';

const job = (overrides) => ({ job_type: 'ingest_github', status: 'running', stage: 'cloning', build_graph: false, graph_status: 'not_requested', ...overrides });

describe('job state', () => {
  it('only completed and failed are terminal (polling stops there)', () => {
    expect(['queued', 'running'].map((status) => isTerminal({ status }))).toEqual([false, false]);
    expect(['completed', 'failed'].map((status) => isTerminal({ status }))).toEqual([true, true]);
    expect(isTerminal(undefined)).toBe(false);
  });

  it('lists the stages each job type really goes through', () => {
    expect(stagesFor(job())).toEqual(['queued', 'fetching_metadata', 'cloning', 'indexing', 'done']);
    expect(stagesFor(job({ job_type: 'reindex', build_graph: true }))).toEqual(['queued', 'indexing', 'building_graph', 'done']);
  });

  it('marks earlier stages done, the current one running, later ones pending', () => {
    expect(stageStates(job()).map((s) => s.state)).toEqual(['done', 'done', 'current', 'pending', 'pending']);
  });

  it('marks the stage a failed job died in as failed', () => {
    expect(stageStates(job({ status: 'failed', stage: 'indexing' })).map((s) => s.state)).toEqual(['done', 'done', 'done', 'failed', 'pending']);
  });

  it('shows every stage done for a completed job', () => {
    expect(stageStates(job({ status: 'completed', stage: 'done' })).every((s) => s.state === 'done')).toBe(true);
  });

  it('describes the graph outcome without inventing one', () => {
    expect(graphOutcome(job())).toBe('Not requested');
    expect(graphOutcome(job({ graph_status: 'not_run' }))).toMatch(/not run/);
    expect(graphOutcome(job({ graph_status: 'completed' }))).toBe('Built');
  });

  it('backs off exponentially after poll failures, capped', () => {
    expect([0, 1, 2, 3, 10].map(nextPollDelay)).toEqual([2000, 4000, 8000, 15000, 15000]);
    expect(MAX_POLL_FAILURES).toBeGreaterThan(1);
  });

  it('maps every known error code and falls back safely for unknown ones', () => {
    expect(jobErrorMessage('worker_interrupted')).toMatch(/interrupted/);
    expect(jobErrorMessage('indexing_failed')).toMatch(/previous index/);
    expect(jobErrorMessage('<script>alert(1)</script>')).toBe('The request failed.');
  });
});

describe('validateGithubUrl (client-side pre-check)', () => {
  it.each([
    ['https://github.com/octocat/Hello-World', 'octocat/hello-world'],
    ['https://github.com/octocat/hello-world.git', 'octocat/hello-world'],
    ['  https://github.com/a/b/  ', 'a/b'],
  ])('accepts %s', (input, fullName) => {
    expect(validateGithubUrl(input)).toEqual({ ok: true, fullName });
  });

  it.each([
    '', '/etc/passwd', '../../x', 'file:///etc/passwd', 'http://github.com/a/b', 'ssh://github.com/a/b',
    'git@github.com:a/b.git', 'https://github.com.evil.example/a/b', 'https://user:pass@github.com/a/b',
    'https://github.com:8443/a/b', 'https://github.com/a', 'https://github.com/a/b/tree/main', 'https://github.com/a/b?x=1',
    'https://github.com/a/b#readme', 'https://github.com/../b', 'javascript:alert(1)', 'https://github.com/a/b\n', 'x'.repeat(301),
    'https://github.com/a/../b/c', 'https://github.com/a/./b', 'https://github.com/a%2F..%2Fb/c', 'HTTPS://GITHUB.COM.evil.example/a/b',
  ])('rejects %s', (input) => {
    expect(validateGithubUrl(input).ok).toBe(false);
  });
});
