"""Tests for the liveness/readiness distinction (Phase 15) — real
PostgreSQL connectivity checks where practical (not entirely mocked),
since a health check's whole job is to reflect real connectivity.
"""

from api.health import check_liveness, check_readiness

from tests.vectorstore.conftest import TEST_DATABASE_URL, requires_postgres


def test_liveness_never_depends_on_external_services():
    """Liveness must always report alive — it takes no arguments and
    checks nothing external, by construction (there is nothing here
    that COULD make it depend on a service).
    """
    result = check_liveness()
    assert result == {"status": "alive"}


@requires_postgres
def test_readiness_is_true_when_postgres_is_reachable(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.delenv("NEO4J_URI", raising=False)

    result = check_readiness()

    assert result.ready is True
    postgres_status = next(d for d in result.dependencies if d.name == "postgres")
    assert postgres_status.ok is True
    assert postgres_status.detail == "reachable"


def test_readiness_is_false_when_postgres_is_unreachable(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://baduser@127.0.0.1:1/does_not_exist")

    result = check_readiness()

    assert result.ready is False
    postgres_status = next(d for d in result.dependencies if d.name == "postgres")
    assert postgres_status.ok is False


def test_readiness_is_false_when_database_url_is_not_configured_at_all(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    result = check_readiness()

    assert result.ready is False
    postgres_status = next(d for d in result.dependencies if d.name == "postgres")
    assert "not configured" in postgres_status.detail


@requires_postgres
def test_a_missing_neo4j_configuration_does_not_block_readiness(monkeypatch):
    """The load-bearing property this section exists to prove: Neo4j
    being entirely unconfigured must not make the whole process appear
    unready, since it's an OPTIONAL dependency (Phase 5/6).
    """
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_USERNAME", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)

    result = check_readiness()

    assert result.ready is True
    neo4j_status = next(d for d in result.dependencies if d.name == "neo4j")
    assert neo4j_status.ok is True
    assert "optional" in neo4j_status.detail


@requires_postgres
def test_a_configured_but_unreachable_neo4j_does_not_block_readiness(monkeypatch):
    """A temporary Neo4j outage must not make the entire process appear
    dead/unready — only Postgres (the one REQUIRED dependency) gates
    overall readiness.
    """
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:1")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "wrong")

    result = check_readiness()

    assert result.ready is True  # overall readiness unaffected
    neo4j_status = next(d for d in result.dependencies if d.name == "neo4j")
    assert neo4j_status.ok is False  # but the individual status is honestly reported


def test_readiness_never_includes_the_database_url_value(monkeypatch):
    secret_looking_url = "postgresql://user:supersecretpassword123@127.0.0.1:1/db"
    monkeypatch.setenv("DATABASE_URL", secret_looking_url)

    result = check_readiness()

    for dependency in result.dependencies:
        assert "supersecretpassword123" not in dependency.detail
