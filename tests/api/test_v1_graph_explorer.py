"""Knowledge-graph exploration endpoints (frontend phase F5).

Real-Neo4j tests build a REAL graph with GraphBuilder from a temporary
repository (and delete exactly those nodes afterwards, like
tests/graph/conftest.py). Availability states use the existing
`get_neo4j_client_factory` override. Nothing here fabricates graph data.
"""

import pytest

from api.dependencies import get_neo4j_client_factory
from api.v1 import API_V1_PREFIX
from graph.builder import GraphBuilder
from graph.client import Neo4jClient
from graph.exceptions import MissingCredentialsError
from graph.schema import apply_constraints
from ingestion.service import IngestionService
from parsing.service import ParsingService

from tests.api.conftest import TEST_DATABASE_URL, auth_headers, insert_repository, requires_postgres
from tests.graph.conftest import requires_neo4j

pytestmark = requires_postgres

FAKE_ID = "4:00000000-0000-0000-0000-000000000000:7"


def _url(repository_id, path):
    return f"{API_V1_PREFIX}/repositories/{repository_id}/graph/{path}"


def _write_repo(root, extra_files=0):
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "base.py").write_text("class Base:\n    def run(self):\n        return 1\n")
    (root / "pkg" / "impl.py").write_text(
        "from pkg.base import Base\nimport os\n\n\nclass Impl(Base):\n    def run(self):\n        return 2\n\n\ndef helper():\n    return os.sep\n"
    )
    for i in range(extra_files):
        (root / f"extra_{i:02d}.py").write_text(f"def f{i}():\n    return {i}\n")
    return root


@pytest.fixture
def graph_repo(app, monkeypatch, vector_store, tmp_path):  # noqa: F811
    """A repository indexed in Postgres AND built into the real Neo4j."""
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    root = _write_repo((tmp_path / "graphrepo").resolve(), extra_files=6)
    repository_id = insert_repository(vector_store, str(root), name="graphrepo")
    client = Neo4jClient()
    apply_constraints(client)
    ingestion = IngestionService().ingest(str(root))
    GraphBuilder(client).build(str(root), ingestion, ParsingService().parse_repository(str(root), ingestion))
    yield repository_id, root
    client.run("MATCH (n {repository_root_path: $p}) DETACH DELETE n", p=str(root))
    client.run("MATCH (r:Repository {root_path: $p}) DETACH DELETE r", p=str(root))
    client.close()


def _ids_by_label(body):
    return {n["label"]: n["id"] for n in body["nodes"]}


@requires_neo4j
def test_repository_overview_returns_real_nodes_and_contains_edges(api_client, graph_repo):
    repository_id, root = graph_repo

    response = api_client.get(_url(repository_id, "neighborhood"), headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["focus"]["kind"] == "Repository" and body["focus"]["label"] == "graphrepo"
    labels = {n["label"] for n in body["nodes"]} - {"graphrepo"}
    assert labels == {"pkg"} | {f"extra_{i:02d}.py" for i in range(6)}
    assert {e["type"] for e in body["edges"]} == {"CONTAINS"}
    assert all(e["source"] == body["focus"]["id"] for e in body["edges"])
    assert body["total_neighbors"] == 7 and body["truncated"] is False
    assert body["relationship_types"] == ["CONTAINS", "DEFINES", "IMPORTS", "RESOLVES_TO", "INHERITS"]


@requires_neo4j
def test_class_neighbourhood_has_defines_and_inherits_and_never_calls(api_client, graph_repo):
    repository_id, _ = graph_repo
    found = api_client.get(_url(repository_id, "search"), params={"q": "Impl"}, headers=auth_headers()).json()
    impl_class = next(n for n in found["results"] if n["kind"] == "Class")
    assert (impl_class["relative_path"], impl_class["start_line"], impl_class["end_line"]) == ("pkg/impl.py", 5, 7)

    body = api_client.get(_url(repository_id, "neighborhood"), params={"node": impl_class["id"]}, headers=auth_headers()).json()

    neighbours = {(e["type"], next(n["label"] for n in body["nodes"] if n["id"] in (e["source"], e["target"]) and n["id"] != impl_class["id"])) for e in body["edges"]}
    assert neighbours == {("DEFINES", "impl.py"), ("DEFINES", "Impl.run"), ("INHERITS", "Base")}
    inherits = next(e for e in body["edges"] if e["type"] == "INHERITS")
    assert inherits["source"] == impl_class["id"]  # direction preserved: Impl -> Base
    assert "CALLS" not in {e["type"] for e in body["edges"]}


@requires_neo4j
def test_file_neighbourhood_includes_imports_of_external_and_internal_modules(api_client, graph_repo):
    repository_id, _ = graph_repo
    impl_file = next(
        n for n in api_client.get(_url(repository_id, "search"), params={"q": "pkg/impl"}, headers=auth_headers()).json()["results"]
        if n["kind"] == "File"
    )

    body = api_client.get(_url(repository_id, "neighborhood"), params={"node": impl_file["id"]}, headers=auth_headers()).json()

    imported = {n["label"] for n in body["nodes"] if n["kind"] == "Module"}
    # Exactly what Phase 2 stored: `from pkg.base import Base` is recorded as
    # the module "pkg.base.Base" — returned as-is, not reinterpreted.
    assert imported == {"pkg.base.Base", "os"}
    assert impl_file["language"] == "Python"


@requires_neo4j
def test_server_side_limit_truncates_and_reports_it(api_client, graph_repo):
    repository_id, _ = graph_repo

    body = api_client.get(_url(repository_id, "neighborhood"), params={"limit": 3}, headers=auth_headers()).json()

    assert len(body["nodes"]) == 4 and len(body["edges"]) == 3  # focus + 3
    assert body["total_neighbors"] == 7 and body["truncated"] is True and body["limit"] == 3


@requires_neo4j
def test_no_server_paths_or_unlisted_properties_are_returned(api_client, graph_repo):
    repository_id, root = graph_repo
    overview = api_client.get(_url(repository_id, "neighborhood"), headers=auth_headers())
    search = api_client.get(_url(repository_id, "search"), params={"q": "run"}, headers=auth_headers())

    for response in (overview, search):
        text = response.text
        assert str(root) not in text
        for forbidden in ("root_path", "repository_root_path", "parameters", "base_class_names", "size_bytes"):
            assert forbidden not in text


@requires_neo4j
def test_a_node_from_another_repository_is_not_reachable(api_client, graph_repo, vector_store, tmp_path):
    repository_id, _ = graph_repo
    other_root = _write_repo((tmp_path / "other").resolve())
    other_id = insert_repository(vector_store, str(other_root), name="other")
    client = Neo4jClient()
    try:
        ingestion = IngestionService().ingest(str(other_root))
        GraphBuilder(client).build(str(other_root), ingestion, ParsingService().parse_repository(str(other_root), ingestion))
        foreign = api_client.get(_url(other_id, "search"), params={"q": "Base"}, headers=auth_headers()).json()["results"][0]["id"]

        response = api_client.get(_url(repository_id, "neighborhood"), params={"node": foreign}, headers=auth_headers())

        assert (response.status_code, response.json()) == (404, {"error": "node_not_found"})
    finally:
        client.run("MATCH (n {repository_root_path: $p}) DETACH DELETE n", p=str(other_root))
        client.run("MATCH (r:Repository {root_path: $p}) DETACH DELETE r", p=str(other_root))
        client.close()


@requires_neo4j
def test_cypher_cannot_be_injected_through_any_parameter(api_client, graph_repo):
    repository_id, root = graph_repo
    client = Neo4jClient()
    count = lambda: client.run("MATCH (n {repository_root_path: $p}) RETURN count(n) AS n", p=str(root))[0]["n"]  # noqa: E731
    try:
        before = count()
        payload = "') DETACH DELETE n //"
        responses = [
            api_client.get(_url(repository_id, "search"), params={"q": payload}, headers=auth_headers()),
            api_client.get(_url(repository_id, "search"), params={"q": "x' OR 1=1 //"}, headers=auth_headers()),
            api_client.get(_url(repository_id, "neighborhood"), params={"node": "1 OR true", "cypher": "MATCH (n) DETACH DELETE n"}, headers=auth_headers()),
            api_client.get(_url(repository_id, "neighborhood"), params={"cypher": "MATCH (n) DETACH DELETE n"}, headers=auth_headers()),
        ]
        assert [r.status_code for r in responses] == [200, 200, 400, 200]
        assert responses[0].json()["results"] == [] and responses[1].json()["results"] == []
        assert count() == before
    finally:
        client.close()


@requires_neo4j
def test_repository_without_a_graph_reports_not_built(api_client, app, monkeypatch, vector_store, tmp_path):  # noqa: F811
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    repository_id = insert_repository(vector_store, str((tmp_path / "nograph").resolve()), name="nograph")
    for path, params in (("neighborhood", {}), ("search", {"q": "x"})):
        response = api_client.get(_url(repository_id, path), params=params, headers=auth_headers())
        assert (response.status_code, response.json()) == (404, {"error": "graph_not_built"})


# --- validation and availability (no real Neo4j needed) ------------------


@pytest.fixture
def plain_repo(app, monkeypatch, vector_store, tmp_path):  # noqa: F811
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    return insert_repository(vector_store, str(tmp_path.resolve()), name="plain")


@pytest.mark.parametrize("node", ["1 OR true", "MATCH (n) RETURN n", "4:not-a-uuid:1", "../../etc", "4:" + "a" * 36 + ":1", "x" * 101])
def test_malformed_node_ids_are_rejected_before_any_query(api_client, app, plain_repo, node):
    calls = []
    app.dependency_overrides[get_neo4j_client_factory] = lambda: (lambda: calls.append("connected"))
    response = api_client.get(_url(plain_repo, "neighborhood"), params={"node": node}, headers=auth_headers())
    assert response.status_code in (400, 422)
    assert calls == []


@pytest.mark.parametrize(
    "path, params",
    [
        ("neighborhood", {"limit": 0}),
        ("neighborhood", {"limit": 201}),
        ("search", {"q": "x", "limit": 51}),
        ("search", {"q": ""}),
        ("search", {"q": "q" * 101}),
        ("search", {}),
    ],
)
def test_limits_and_lengths_are_enforced_server_side(api_client, plain_repo, path, params):
    response = api_client.get(_url(plain_repo, path), params=params, headers=auth_headers())
    assert response.status_code == 422


def test_control_characters_in_search_are_rejected(api_client, app, plain_repo):
    app.dependency_overrides[get_neo4j_client_factory] = lambda: (lambda: pytest.fail("must not connect"))
    response = api_client.get(_url(plain_repo, "search"), params={"q": "a\x00b"}, headers=auth_headers())
    assert (response.status_code, response.json()) == (422, {"error": "invalid_query"})


def test_graph_not_configured_and_unavailable_are_distinct(api_client, app, plain_repo):
    def missing():
        raise MissingCredentialsError("NEO4J_URI ... must all be set")

    class Broken:
        def run(self, *args, **kwargs):
            raise RuntimeError("bolt://neo4j:hunter2@10.1.2.3 refused")

        def close(self):
            pass

    for factory, code in ((missing, "graph_not_configured"), (Broken, "graph_unavailable")):
        app.dependency_overrides[get_neo4j_client_factory] = lambda f=factory: f
        for path, params in (("neighborhood", {}), ("search", {"q": "x"})):
            response = api_client.get(_url(plain_repo, path), params=params, headers=auth_headers())
            assert (response.status_code, response.json()) == (503, {"error": code})
            assert "hunter2" not in response.text and "10.1.2.3" not in response.text


def test_unknown_repository_is_404(api_client, plain_repo):
    assert api_client.get(_url(999999, "neighborhood"), headers=auth_headers()).json() == {"error": "repository_not_found"}


def test_graph_endpoints_require_the_service_token(api_client, plain_repo):
    for path in ("neighborhood", "search?q=x"):
        assert api_client.get(_url(plain_repo, path)).status_code == 401
