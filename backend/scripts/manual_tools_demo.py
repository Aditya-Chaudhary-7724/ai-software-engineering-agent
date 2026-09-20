"""Manual, human-readable demonstration of Phase 8 repository tools.

Exercises every registered tool against one small repository,
including a deliberate path-traversal attempt (to show the security
boundary actually rejects it) and get_callers (to show it honestly
reports unavailability rather than guessing).

Requires reachable PostgreSQL (with pgvector) and Neo4j. Cleans up
everything it creates.

Run from the repository root:

    set -a; source .env; set +a
    .venv/bin/python backend/scripts/manual_tools_demo.py
"""

import getpass
import json
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

from tools.service import build_default_registry

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


def show(registry, name: str, input_dict: dict) -> None:
    result = registry.invoke(name, input_dict)
    print(f"--- {name}({input_dict}) ---")
    if result.success:
        print(json.dumps(result.data, indent=2)[:600])
    else:
        print(f"REJECTED: {result.error}")
    print()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="tools-demo-") as tmp_dir:
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

            registry = build_default_registry(vector_store, neo4j_client, embedding_provider)
            root_path = str(root.resolve())

            show(registry, "list_files", {"root_path": root_path})
            show(registry, "read_file", {"root_path": root_path, "relative_path": "auth_service.py"})
            show(registry, "analyze_code", {"root_path": root_path, "relative_path": "routes.py"})
            show(
                registry,
                "search_code",
                {"repository_id": index_result.repository_id, "query": "login authentication"},
            )
            show(registry, "search_symbol", {"root_path": root_path, "name": "login_user"})
            show(registry, "graph_query", {"root_path": root_path, "query_type": "repository_summary"})
            show(registry, "get_dependencies", {"root_path": root_path, "relative_path": "routes.py"})

            print("=== Security boundary: path traversal attempt ===")
            show(registry, "read_file", {"root_path": root_path, "relative_path": "../../../etc/passwd"})

            print("=== Honest limitation: get_callers ===")
            show(
                registry,
                "get_callers",
                {"root_path": root_path, "relative_path": "auth_service.py", "symbol_name": "generate_jwt"},
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
