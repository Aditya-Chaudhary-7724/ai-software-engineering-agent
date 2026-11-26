-- Runs once, when the PostgreSQL volume is first initialised. The schema
-- itself is applied by backend/scripts/apply_schema.py (deploy.sh), but
-- VectorStore registers the pgvector type on connect, which needs the
-- extension to exist first (the same provisioning step CI performs).
CREATE EXTENSION IF NOT EXISTS vector;
