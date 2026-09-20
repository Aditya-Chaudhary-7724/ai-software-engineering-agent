"""Manual, human-readable demonstration of Phase 9 code modification.

Runs the full safe-modification workflow standalone (not through the
agent, to keep this demo focused on modification itself):

    instruction -> find affected file -> generate proposed content
                -> unified diff -> [shown here] -> apply
                -> stale-change safety check (demonstrated separately)

Uses DeterministicLocalEmbeddingProvider and StubLLMProvider (both
explicitly non-production stand-ins): the proposed content is a fixed
placeholder, not valid code — this demonstrates that the propose ->
diff -> apply mechanism and its safety checks work correctly, not
real code-generation quality (that needs a real LLM_API_KEY).

Everything happens inside a temporary directory that is deleted when
this script exits — the real project repository is never touched.

Requires reachable PostgreSQL (with pgvector).

Run from the repository root:

    set -a; source .env; set +a
    .venv/bin/python backend/scripts/manual_modification_demo.py
"""

import getpass
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore

from modification.exceptions import StaleChangeError
from modification.service import ModificationService
from rag.llm.stub_provider import StubLLMProvider

DATABASE_URL = os.environ.get(
    "DATABASE_URL", f"postgresql://{getpass.getuser()}@localhost:5432/ai_swe_agent"
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="modification-demo-") as tmp_dir:
        root = Path(tmp_dir)
        (root / "main.py").write_text("def greet():\n    return 'hi'\n")

        ingestion_result = IngestionService().ingest(str(root))
        parsing_result = ParsingService().parse_repository(str(root), ingestion_result)

        embedding_provider = DeterministicLocalEmbeddingProvider()
        vector_store = VectorStore(DATABASE_URL)
        index_result = IndexingService(embedding_provider, vector_store).index_repository(str(root))

        try:
            service = ModificationService(vector_store, embedding_provider, StubLLMProvider())

            print("--- 1. Propose a change (no write yet) ---")
            proposal = service.propose_change(
                str(root), index_result.repository_id, "fix the greet function to say hello"
            )
            print(f"Affected file: {proposal.relative_path}")
            print("Diff (this is what a human reviewer would see BEFORE approving):")
            print(proposal.diff)
            print()
            print(f"On disk, unchanged: {(root / 'main.py').read_text()!r}")
            print()

            print("--- 2. Apply the approved change ---")
            result = service.apply_change(str(root), proposal)
            print(result.message)
            print(f"On disk, now: {(root / 'main.py').read_text()!r}")
            print()

            print("--- 3. Safety check: refuse a stale proposal ---")
            (root / "main.py").write_text("def greet():\n    return 'edited concurrently by someone else'\n")
            try:
                service.apply_change(str(root), proposal)
                print("ERROR: should have refused — this should never print")
            except StaleChangeError as exc:
                print(f"Correctly refused: {exc}")
            print(f"Concurrent edit survives untouched: {(root / 'main.py').read_text()!r}")
        finally:
            conn = vector_store.connect()
            with conn.cursor() as cur:
                cur.execute("TRUNCATE code_chunks, repositories RESTART IDENTITY CASCADE;")
            conn.commit()
            conn.close()


if __name__ == "__main__":
    main()
