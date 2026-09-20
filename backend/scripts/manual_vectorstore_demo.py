"""Manual, human-readable demonstration of Phase 3 vector search.

Builds a small synthetic repository, indexes it into PostgreSQL +
pgvector using the deterministic local embedding provider (no API key
needed), and runs a similarity search.

NOTE: the local embedding provider is NOT semantically meaningful (see
vectorstore/embeddings/local_provider.py) — this demonstrates that the
pipeline and pgvector search mechanics work end-to-end, not retrieval
quality. Real semantic retrieval requires EMBEDDING_API_KEY and
OpenAIEmbeddingProvider.

Requires a reachable PostgreSQL with pgvector installed. Reads
DATABASE_URL from the environment, falling back to the local dev
default used throughout this project's tests.

Run from the repository root:

    .venv/bin/python backend/scripts/manual_vectorstore_demo.py
"""

import getpass
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore

DATABASE_URL = os.environ.get(
    "DATABASE_URL", f"postgresql://{getpass.getuser()}@localhost:5432/ai_swe_agent"
)


def build_sample_repository(root: Path) -> None:
    (root / "math_utils.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n\n"
        "def multiply(a, b):\n"
        "    return a * b\n\n"
        "class Calculator:\n"
        "    def divide(self, a, b):\n"
        "        return a / b\n"
    )
    (root / "auth.py").write_text(
        "def check_password(password, hashed):\n"
        "    return password == hashed\n\n"
        "def generate_token(user_id):\n"
        "    return f'token-{user_id}'\n"
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="vectorstore-demo-") as tmp_dir:
        root = Path(tmp_dir)
        build_sample_repository(root)

        provider = DeterministicLocalEmbeddingProvider()
        store = VectorStore(DATABASE_URL)
        service = IndexingService(provider, store)

        result = service.index_repository(str(root))
        print(f"Indexed repository_id={result.repository_id}, chunks_indexed={result.chunks_indexed}")
        print()

        query = "def add(a, b):\n    return a + b"
        query_embedding = provider.embed([query])[0]

        conn = store.connect()
        try:
            hits = store.similarity_search(
                conn, query_embedding, top_k=5, repository_id=result.repository_id
            )
            print(f"Query: {query!r}")
            print("Nearest chunks (distance is hash-based, not semantic):")
            for hit in hits:
                print(
                    f"  {hit.distance:.4f}  {hit.relative_path}:{hit.start_line}-{hit.end_line}  "
                    f"[{hit.chunk_type}] {hit.qualified_name}"
                )
        finally:
            conn.close()


if __name__ == "__main__":
    main()
