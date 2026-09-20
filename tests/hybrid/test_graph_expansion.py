"""LOCAL TESTED against real Neo4j and (for build_graph_candidates)
real PostgreSQL; skips cleanly if either is unreachable.
"""

from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService

from graph.builder import GraphBuilder

from hybrid.graph_expansion import build_graph_candidates, find_related_symbols
from tests.hybrid.conftest import neo4j_client, requires_neo4j, requires_postgres, vector_store


def _build_graph(tmp_path, neo4j_client):
    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    GraphBuilder(neo4j_client).build(str(tmp_path), ingestion_result, parsing_result)
    return ingestion_result, parsing_result


@requires_neo4j
def test_find_related_symbols_for_class_seed(tmp_path, neo4j_client):
    (tmp_path / "animals.py").write_text(
        "class Animal:\n    pass\n\nclass Dog(Animal):\n    def speak(self):\n        pass\n"
    )
    _build_graph(tmp_path, neo4j_client)
    root_path = str(tmp_path.resolve())

    related = find_related_symbols(neo4j_client, root_path, "animals.py", "class", "Dog", "Dog")

    kinds = {(r[0], r[1], r[2]) for r in related}
    assert ("class_method", "animals.py", "Dog.speak") in kinds
    assert ("ancestor_class", "animals.py", "Animal") in kinds


@requires_neo4j
def test_find_related_symbols_for_function_seed_finds_siblings(tmp_path, neo4j_client):
    (tmp_path / "svc.py").write_text(
        "class Service:\n    def a(self):\n        pass\n    def b(self):\n        pass\n"
    )
    _build_graph(tmp_path, neo4j_client)
    root_path = str(tmp_path.resolve())

    related = find_related_symbols(neo4j_client, root_path, "svc.py", "function", "a", "Service.a")

    assert ("parent_class", "svc.py", "Service") in related
    assert ("sibling_method", "svc.py", "Service.b") in related


@requires_neo4j
def test_find_related_symbols_finds_dependency_chain(tmp_path, neo4j_client):
    (tmp_path / "auth_service.py").write_text("def generate_jwt():\n    pass\n")
    (tmp_path / "routes.py").write_text(
        "from auth_service import generate_jwt\n\ndef login():\n    return generate_jwt()\n"
    )
    _build_graph(tmp_path, neo4j_client)
    root_path = str(tmp_path.resolve())

    related = find_related_symbols(neo4j_client, root_path, "routes.py", "function", "login", "login")

    assert ("dependency_symbol", "auth_service.py", "generate_jwt") in related


@requires_postgres
@requires_neo4j
def test_build_graph_candidates_resolves_to_real_chunks(tmp_path, vector_store, neo4j_client):
    (tmp_path / "auth_service.py").write_text(
        "def login_user():\n    return generate_jwt()\n\ndef generate_jwt():\n    return 'token'\n"
    )
    (tmp_path / "routes.py").write_text(
        "from auth_service import login_user\n\ndef login_route():\n    return login_user()\n"
    )

    ingestion_result, parsing_result = _build_graph(tmp_path, neo4j_client)

    embedding_provider = DeterministicLocalEmbeddingProvider()
    index_result = IndexingService(embedding_provider, vector_store).index_repository(str(tmp_path))

    conn = vector_store.connect()
    try:
        from rag.models import Candidate

        seed = Candidate(
            chunk_id=0, relative_path="routes.py", language="Python", chunk_type="function",
            symbol_name="login_route", qualified_name="login_route", start_line=3, end_line=4,
            content="...", vector_similarity=1.0, keyword_score=0.0, combined_score=1.0,
        )
        root_path = str(tmp_path.resolve())
        graph_candidates = build_graph_candidates(
            neo4j_client, conn, index_result.repository_id, root_path, [seed]
        )
    finally:
        conn.close()

    symbol_names = {c.symbol_name for c in graph_candidates}
    assert "login_user" in symbol_names
    assert "generate_jwt" in symbol_names
    assert all("graph:" in c.found_via[0] for c in graph_candidates)
