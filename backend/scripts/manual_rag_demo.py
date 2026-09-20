"""Manual, human-readable demonstration of Phase 4 code RAG.

Indexes a small synthetic repository (Phase 1-3) and answers a
repository-level question through the full retrieval -> ranking ->
context -> "LLM" pipeline, using:

- DeterministicLocalEmbeddingProvider (no EMBEDDING_API_KEY needed)
- StubLLMProvider (no LLM_API_KEY needed)

Both are explicitly non-production stand-ins (see their docstrings).
This demonstrates that retrieval, ranking, and grounded source
citations work correctly end-to-end — NOT real answer quality, which
requires real EMBEDDING_API_KEY and LLM_API_KEY credentials.

Requires a reachable PostgreSQL with pgvector installed.

Run from the repository root:

    .venv/bin/python backend/scripts/manual_rag_demo.py
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

from rag.llm.stub_provider import StubLLMProvider
from rag.service import RAGService

DATABASE_URL = os.environ.get(
    "DATABASE_URL", f"postgresql://{getpass.getuser()}@localhost:5432/ai_swe_agent"
)


def build_sample_repository(root: Path) -> None:
    (root / "auth.py").write_text(
        "def authenticate_user(username, password):\n"
        "    \"\"\"Checks credentials against the user store.\"\"\"\n"
        "    return check_credentials(username, password)\n\n"
        "def generate_session_token(user_id):\n"
        "    return f'token-{user_id}'\n"
    )
    (root / "math_utils.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n\n"
        "def multiply(a, b):\n"
        "    return a * b\n"
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="rag-demo-") as tmp_dir:
        root = Path(tmp_dir)
        build_sample_repository(root)

        embedding_provider = DeterministicLocalEmbeddingProvider()
        store = VectorStore(DATABASE_URL)
        indexing_service = IndexingService(embedding_provider, store)
        index_result = indexing_service.index_repository(str(root))
        print(f"Indexed {index_result.chunks_indexed} chunks into repository_id={index_result.repository_id}")
        print()

        rag_service = RAGService(store, embedding_provider, StubLLMProvider())
        question = "Where is authentication implemented?"
        answer = rag_service.answer(question, repository_id=index_result.repository_id)

        print(f"Question: {question}")
        print(f"Answer: {answer.answer}")
        print()
        print(f"Sources ({answer.context_chunk_count} chunks used):")
        for source in answer.sources:
            print(
                f"  {source.relative_path}:{source.start_line}-{source.end_line} "
                f"[{source.chunk_type}] {source.symbol_name}"
            )


if __name__ == "__main__":
    main()
