-- Phase 3 schema. Applied idempotently by migrations.py.
--
-- `code_chunks` denormalizes relative_path/language onto every row
-- (rather than joining to a separate `files` table) because every
-- retrieval query filters/reads those columns directly; a join would
-- be pure overhead for no benefit at this schema's current size.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS repositories (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL UNIQUE,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS code_chunks (
    id BIGSERIAL PRIMARY KEY,
    repository_id BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    language TEXT,
    chunk_type TEXT NOT NULL,
    symbol_name TEXT,
    qualified_name TEXT,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(1536) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_code_chunks_repository_id ON code_chunks (repository_id);
CREATE INDEX IF NOT EXISTS idx_code_chunks_language ON code_chunks (language);
CREATE INDEX IF NOT EXISTS idx_code_chunks_chunk_type ON code_chunks (chunk_type);

-- Approximate nearest-neighbor index for cosine similarity search.
-- HNSW is chosen over IVFFlat because it needs no training/list-count
-- tuning step and performs well at the small-to-medium row counts this
-- project will have; IVFFlat only pays off at much larger scale.
CREATE INDEX IF NOT EXISTS idx_code_chunks_embedding_hnsw
    ON code_chunks USING hnsw (embedding vector_cosine_ops);

-- Full-text search index used by Phase 4's keyword retrieval
-- (backend/rag/keyword_search.py). Computed as an expression index
-- rather than a stored generated column since it needs no separate
-- migration if the search configuration ('english') ever changes.
CREATE INDEX IF NOT EXISTS idx_code_chunks_content_fts
    ON code_chunks USING gin (to_tsvector('english', content));

-- Frontend phase F3: background jobs (GitHub ingestion / re-index), run by
-- the API process's in-process worker (backend/jobs/). Postgres is the
-- queue — no Redis/Celery: claiming uses FOR UPDATE SKIP LOCKED, so several
-- API processes can each run a worker safely.
--
-- `target_path` is the SERVER-RESOLVED repository root a job writes to. It
-- is never returned by the API. The partial unique index below allows at
-- most ONE queued/running job per target: duplicate submissions and
-- concurrent re-indexes of the same repository are rejected atomically by
-- the database, not by a check-then-insert race in application code.
CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY,
    job_type TEXT NOT NULL CHECK (job_type IN ('ingest_github', 'reindex')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    stage TEXT NOT NULL,
    target_path TEXT NOT NULL,
    github_full_name TEXT,
    repository_id BIGINT REFERENCES repositories(id) ON DELETE SET NULL,
    build_graph BOOLEAN NOT NULL DEFAULT FALSE,
    graph_status TEXT NOT NULL DEFAULT 'not_requested',
    progress JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    worker_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_jobs_one_active_per_target
    ON jobs (target_path) WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS idx_jobs_status_created_at ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_repository_id ON jobs (repository_id);

-- Frontend phase F6: agent tasks (explicit Task workflow, separate from the
-- read-only Ask) and their human-approval state. Run by a second instance
-- of the same in-process worker loop as `jobs` (backend/agent_tasks/).
-- The agent's own LangGraph state lives in LangGraph's checkpoint tables
-- (created by PostgresSaver.setup(), see backend/agent/checkpointing.py);
-- this table is the queue, the approval gate, and the safe client view.
--
-- Approval gate: a decision is accepted only by ONE conditional UPDATE that
-- requires status = 'awaiting_approval', the pending interrupt id, the
-- pending diff hash, and an unexpired approval window — so a stale, changed,
-- reused, expired or concurrent second decision matches zero rows.
--
-- `target_path` is the server-resolved repository root and is never
-- returned by the API. One ACTIVE task per repository (partial unique
-- index): two agent tasks can never change the same checkout concurrently.
CREATE TABLE IF NOT EXISTS agent_tasks (
    id UUID PRIMARY KEY,
    repository_id BIGINT NOT NULL,
    target_path TEXT NOT NULL,
    instruction TEXT NOT NULL,
    route TEXT NOT NULL CHECK (route IN ('answer', 'modify')),
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'running', 'awaiting_approval', 'completed', 'rejected', 'failed', 'expired', 'interrupted'
    )),
    -- What the worker does when it claims a queued task: start the graph, or
    -- resume it with the decision recorded below.
    action TEXT NOT NULL DEFAULT 'start' CHECK (action IN ('start', 'resume')),
    decision_approved BOOLEAN,
    decided_at TIMESTAMPTZ,
    approvals_granted INTEGER NOT NULL DEFAULT 0,
    -- The most recent proposal (pending while status = 'awaiting_approval').
    proposal_interrupt_id TEXT,
    proposal_diff TEXT,
    proposal_diff_sha256 TEXT,
    proposal_relative_path TEXT,
    proposal_message TEXT,
    proposal_fix_iteration INTEGER,
    approval_expires_at TIMESTAMPTZ,
    execution_log JSONB NOT NULL DEFAULT '[]'::jsonb,
    final_response TEXT,
    sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    outcome JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    worker_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_agent_tasks_one_active_per_repository
    ON agent_tasks (repository_id) WHERE status IN ('queued', 'running', 'awaiting_approval');
CREATE INDEX IF NOT EXISTS idx_agent_tasks_status_created_at ON agent_tasks (status, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_repository_id ON agent_tasks (repository_id);
