"""Phase 15: the EXPLICIT database migration step.

`vectorstore.migrations.apply_schema` (Phase 3) is fully idempotent —
`CREATE TABLE/INDEX IF NOT EXISTS` only, never `DROP`, never
`TRUNCATE`, safe to run any number of times. It was previously only
ever invoked implicitly, inside `IndexingService.index_repository`
(i.e. schema application happened as a side effect of the first real
ingestion call, not as its own deliberate step).

For production, schema changes should be a deliberate, observable,
single step — run once per deploy (a CI/CD "migrate" job, or manually
before starting new application instances), NOT hidden inside
application startup or triggered implicitly by the first request that
happens to need it. This script is that explicit step. It does nothing
destructive; running it twice, or against a database that already has
the schema, is always safe.

Run from the repository root, against a real `DATABASE_URL`:

    set -a; source .env; set +a
    .venv/bin/python backend/scripts/apply_schema.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from vectorstore.migrations import apply_schema
from vectorstore.store import VectorStore


def main() -> None:
    missing = config.missing_required_settings()
    if missing:
        print(f"FAILED: required setting(s) missing: {', '.join(missing)}", file=sys.stderr)
        print("Run backend/scripts/check_production_config.py for the full picture.", file=sys.stderr)
        sys.exit(1)

    import os

    database_url = os.environ["DATABASE_URL"]
    vector_store = VectorStore(database_url)
    conn = vector_store.connect()
    try:
        apply_schema(conn)
        print("Schema applied (idempotent — CREATE ... IF NOT EXISTS only, nothing destructive).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
