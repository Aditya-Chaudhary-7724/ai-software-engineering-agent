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
