"""Graph summary endpoint. Neo4j availability states are exercised with
an injected client factory (FastAPI dependency override); one test runs
against a real Neo4j when reachable."""

from api.dependencies import get_neo4j_client_factory
from api.v1 import API_V1_PREFIX
from graph.exceptions import MissingCredentialsError
from ingestion.service import IngestionService
from parsing.service import ParsingService

from tests.api.conftest import auth_headers, requires_postgres
from tests.graph.conftest import requires_neo4j


class _FakeNeo4jClient:
    def __init__(self, exists: bool = True, fail: bool = False) -> None:
        self.exists = exists
        self.fail = fail
        self.queried_root_paths: list = []
        self.closed = False

    def run(self, query: str, **params):
        if self.fail:
            raise RuntimeError("ServiceUnavailable: bolt://neo4j:leaky_password@10.0.0.5:7687")
        self.queried_root_paths.append(params.get("root_path"))
        if "count(r)" in query:
            return [{"n": 1 if self.exists else 0}]
        return [{"folders": 2, "files": 5, "classes": 1, "functions": 7}]

    def close(self) -> None:
        self.closed = True


def _summary(api_client, repository_id):
    return api_client.get(f"{API_V1_PREFIX}/repositories/{repository_id}/graph/summary", headers=auth_headers())


@requires_postgres
def test_graph_summary_uses_the_trusted_root_path_and_reports_counts(app, api_client, registered_repository):
    repository_id, root_path = registered_repository
    fake = _FakeNeo4jClient()
    app.dependency_overrides[get_neo4j_client_factory] = lambda: (lambda: fake)

    response = _summary(api_client, repository_id)

    assert response.status_code == 200
    assert response.json() == {
        "repository_id": repository_id, "built": True, "folders": 2, "files": 5, "classes": 1, "functions": 7,
    }
    assert set(fake.queried_root_paths) == {root_path}
    assert fake.closed is True
    assert root_path not in response.text


@requires_postgres
def test_graph_not_built_is_distinguished_from_empty(app, api_client, registered_repository):
    repository_id, _ = registered_repository
    app.dependency_overrides[get_neo4j_client_factory] = lambda: (lambda: _FakeNeo4jClient(exists=False))

    body = _summary(api_client, repository_id).json()

    assert body["built"] is False
    assert (body["files"], body["functions"]) == (0, 0)


@requires_postgres
def test_graph_not_configured_is_a_specific_503(app, api_client, registered_repository):
    repository_id, _ = registered_repository

    def _missing():
        raise MissingCredentialsError("NEO4J_URI, NEO4J_USERNAME, and NEO4J_PASSWORD must all be set")

    app.dependency_overrides[get_neo4j_client_factory] = lambda: _missing

    response = _summary(api_client, repository_id)

    assert response.status_code == 503
    assert response.json() == {"error": "graph_not_configured"}


@requires_postgres
def test_graph_driver_errors_never_leak_connection_details(app, api_client, registered_repository):
    repository_id, _ = registered_repository
    fake = _FakeNeo4jClient(fail=True)
    app.dependency_overrides[get_neo4j_client_factory] = lambda: (lambda: fake)

    response = _summary(api_client, repository_id)

    assert response.status_code == 503
    assert response.json() == {"error": "graph_unavailable"}
    assert "leaky_password" not in response.text and "10.0.0.5" not in response.text
    assert fake.closed is True


@requires_postgres
@requires_neo4j
def test_graph_summary_against_real_neo4j(api_client, registered_repository, sample_repository):
    from graph.builder import GraphBuilder
    from graph.client import Neo4jClient
    from graph.schema import apply_constraints

    repository_id, root_path = registered_repository
    client = Neo4jClient()
    try:
        apply_constraints(client)
        ingestion = IngestionService().ingest(root_path)
        GraphBuilder(client).build(root_path, ingestion, ParsingService().parse_repository(root_path, ingestion))

        body = _summary(api_client, repository_id).json()

        assert body["built"] is True
        assert body["files"] == len(ingestion.files)
        assert body["functions"] >= 1
    finally:
        client.run("MATCH (n {repository_root_path: $root_path}) DETACH DELETE n", root_path=root_path)
        client.run("MATCH (r:Repository {root_path: $root_path}) DETACH DELETE r", root_path=root_path)
        client.close()
