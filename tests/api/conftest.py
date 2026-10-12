"""Shared fixtures for the `/api/v1` tests.

`api_client` runs the REAL application (`api.main.create_app`) in
process via `TestClient`, with a configured service token. Only the
trace directory and the Neo4j client factory are swapped, through
FastAPI's own `dependency_overrides` — never by patching module
internals.
"""

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_trace_directory
from api.main import create_app
from vectorstore.models import CodeChunk

from tests.vectorstore.conftest import TEST_DATABASE_URL, requires_postgres, vector_store  # noqa: F401

TEST_SERVICE_TOKEN = "test-service-token-" + "x" * 40


def auth_headers(token: str = TEST_SERVICE_TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def app(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("API_SERVICE_TOKEN", TEST_SERVICE_TOKEN)
    application = create_app()
    # Never read the developer's real .observability/traces in tests.
    empty_trace_dir = tmp_path_factory.mktemp("no-traces")
    application.dependency_overrides[get_trace_directory] = lambda: empty_trace_dir
    return application


@pytest.fixture
def api_client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def sample_repository(tmp_path):
    """A repository on disk containing ordinary source plus every kind of
    file the API must refuse to serve."""
    root = tmp_path / "sample-repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def greet(name):\n    return f'hello {name}'\n")
    (root / "src" / "util.js").write_text("export const add = (a, b) => a + b;\n")
    (root / ".env").write_text("DATABASE_PASSWORD=hunter2-should-never-leak\n")
    (root / "config").mkdir()
    (root / "config" / ".env.production").write_text("API_KEY=sk-should-never-leak\n")
    (root / "deploy.pem").write_text("-----BEGIN PRIVATE KEY-----\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[remote]\n url = https://user:ghp_secret@github.com/x/y\n")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1;\n")
    (root / "big.py").write_text("x = 1\n" * 200_000)  # > 1 MB
    (root / "image.dat").write_bytes(b"\x89PNG\x00\x01\x02\xff\xfe")

    outside = tmp_path / "outside-secret.txt"
    outside.write_text("outside the repository root\n")
    (root / "escape_link.txt").symlink_to(outside)
    return root


def insert_repository(vector_store, root_path: str, name: str = "sample-repo", chunks: int = 0) -> int:  # noqa: F811
    conn = vector_store.connect()
    try:
        repository_id = vector_store.get_or_create_repository(conn, name, root_path)
        chunk_rows = [
            CodeChunk(
                relative_path=f"src/module_{i}.py",
                language="python" if i % 2 == 0 else "javascript",
                chunk_type="function",
                symbol_name=f"fn_{i}",
                qualified_name=f"fn_{i}",
                start_line=1,
                end_line=2,
                content=f"def fn_{i}(): pass",
            )
            for i in range(chunks)
        ]
        vector_store.insert_chunks(conn, repository_id, chunk_rows, [[0.0] * 1536 for _ in chunk_rows])
    finally:
        conn.close()
    return repository_id


@pytest.fixture
def registered_repository(monkeypatch, vector_store, sample_repository):  # noqa: F811
    """`sample_repository`, registered in the real test database the same
    way `IndexingService` does (resolved absolute root path)."""
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    root_path = str(sample_repository.resolve())
    repository_id = insert_repository(vector_store, root_path, chunks=3)
    return repository_id, root_path

