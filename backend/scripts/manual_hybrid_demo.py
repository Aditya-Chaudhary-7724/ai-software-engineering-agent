"""Manual, human-readable demonstration of Phase 6 hybrid retrieval.

Recreates this phase's own worked example: a login route that depends
on an auth service, which depends on a JWT utility function. Shows
that graph expansion pulls in `generate_jwt` — a symbol the question
never mentions and that plain vector/keyword search would only find if
it happened to also match textually — because it is structurally
reachable from a matched symbol.

Uses DeterministicLocalEmbeddingProvider and StubLLMProvider (both
explicitly non-production stand-ins — see their docstrings): this
demonstrates that retrieval, graph expansion, ranking, and source
attribution work correctly end-to-end, not real answer quality.

Requires reachable PostgreSQL (with pgvector) and Neo4j.

Run from the repository root:

    set -a; source .env; set +a
    .venv/bin/python backend/scripts/manual_hybrid_demo.py
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

from graph.builder import GraphBuilder
from graph.client import Neo4jClient
from graph.schema import apply_constraints

from rag.llm.stub_provider import StubLLMProvider

from hybrid.service import HybridRAGService

DATABASE_URL = os.environ.get(
    "DATABASE_URL", f"postgresql://{getpass.getuser()}@localhost:5432/ai_swe_agent"
)


def build_sample_repository(root: Path) -> None:
    (root / "auth_service.py").write_text(
        "def login_user(username, password):\n"
        "    return generate_jwt(username)\n\n"
        "def generate_jwt(username):\n"
        "    return f'jwt-for-{username}'\n"
    )
    (root / "routes.py").write_text(
        "from auth_service import login_user\n\n"
        "def login_route(request):\n"
        "    return login_user(request.username, request.password)\n"
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="hybrid-demo-") as tmp_dir:
        root = Path(tmp_dir)
        build_sample_repository(root)

        ingestion_result = IngestionService().ingest(str(root))
        parsing_result = ParsingService().parse_repository(str(root), ingestion_result)

        embedding_provider = DeterministicLocalEmbeddingProvider()
        vector_store = VectorStore(DATABASE_URL)
        index_result = IndexingService(embedding_provider, vector_store).index_repository(str(root))

        neo4j_client = Neo4jClient()
        try:
            apply_constraints(neo4j_client)
            GraphBuilder(neo4j_client).build(str(root), ingestion_result, parsing_result)

            service = HybridRAGService(vector_store, neo4j_client, embedding_provider, StubLLMProvider())
            question = "Where is the login route implemented?"
            answer = service.answer(
                question, repository_id=index_result.repository_id, root_path=str(root.resolve())
            )

            print(f"Question: {question}")
            print(f"Answer: {answer.answer}")
            print()
            print(f"Sources ({answer.context_chunk_count} chunks used):")
            for source in answer.sources:
                via = ", ".join(source.found_via)
                print(
                    f"  {source.relative_path}:{source.start_line}-{source.end_line} "
                    f"[{source.chunk_type}] {source.symbol_name}  (found via: {via})"
                )
        finally:
            conn = vector_store.connect()
            with conn.cursor() as cur:
                cur.execute("TRUNCATE code_chunks, repositories RESTART IDENTITY CASCADE;")
            conn.commit()
            conn.close()

            root_path = str(root.resolve())
            neo4j_client.run("MATCH (n {repository_root_path: $root_path}) DETACH DELETE n", root_path=root_path)
            neo4j_client.run("MATCH (r:Repository {root_path: $root_path}) DETACH DELETE r", root_path=root_path)
            neo4j_client.close()


if __name__ == "__main__":
    main()
