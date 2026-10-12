"""Background jobs (frontend phase F3): a PostgreSQL-backed queue and an
in-process worker that runs repository ingestion and re-indexing off the
HTTP request path. See store.py, worker.py, and ingestion.py."""
