"""Re-indexing regression tests (frontend phase F3), against the real
test PostgreSQL — the property under test IS transaction behavior, so
no part of the database is mocked.

Bug reproduced before the fix: `IndexingService.index_repository`
upserted the repository row and then only INSERTED chunks, each step
committing on its own — so indexing the same repository twice doubled
every chunk, and a failure part-way through could leave a half-written
index.
"""

import threading

import pytest

from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore

from tests.vectorstore.conftest import requires_postgres

pytestmark = requires_postgres


def _write_repo(root, functions=("add", "sub")):
    root.mkdir(parents=True, exist_ok=True)
    body = "".join(f"def {name}(a, b):\n    return a\n\n\n" for name in functions)
    (root / "math_utils.py").write_text(body)
    return root


def _chunk_rows(vector_store, repository_id):
    conn = vector_store.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT relative_path, qualified_name, start_line FROM code_chunks "
                "WHERE repository_id = %s ORDER BY 1, 2, 3",
                (repository_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _service(vector_store):
    return IndexingService(DeterministicLocalEmbeddingProvider(), vector_store)


def test_indexing_the_same_repository_twice_does_not_duplicate_chunks(tmp_path, vector_store):
    root = _write_repo(tmp_path / "repo")
    first = _service(vector_store).index_repository(str(root))
    rows_after_first = _chunk_rows(vector_store, first.repository_id)

    second = _service(vector_store).index_repository(str(root))
    rows_after_second = _chunk_rows(vector_store, second.repository_id)

    assert second.repository_id == first.repository_id
    assert len(rows_after_first) == first.chunks_indexed == 2
    assert rows_after_second == rows_after_first  # same rows, not doubled
    assert len(set(rows_after_second)) == len(rows_after_second)


def test_reindex_replaces_the_index_with_the_current_files(tmp_path, vector_store):
    root = _write_repo(tmp_path / "repo", functions=("add", "sub"))
    repository_id = _service(vector_store).index_repository(str(root)).repository_id

    _write_repo(root, functions=("multiply",))  # 'add'/'sub' removed from disk
    result = _service(vector_store).index_repository(str(root))

    assert result.repository_id == repository_id
    assert [r[1] for r in _chunk_rows(vector_store, repository_id)] == ["multiply"]


def test_reindex_only_touches_its_own_repository(tmp_path, vector_store):
    repo_a = _write_repo(tmp_path / "a", functions=("alpha", "beta"))
    repo_b = _write_repo(tmp_path / "b", functions=("gamma",))
    id_a = _service(vector_store).index_repository(str(repo_a)).repository_id
    id_b = _service(vector_store).index_repository(str(repo_b)).repository_id
    rows_b = _chunk_rows(vector_store, id_b)

    _service(vector_store).index_repository(str(repo_a))
    _service(vector_store).index_repository(str(repo_a))

    assert id_a != id_b
    assert _chunk_rows(vector_store, id_b) == rows_b
    assert len(_chunk_rows(vector_store, id_a)) == 2


class _FailingAfterPartialInsertStore(VectorStore):
    """Inserts only the FIRST new chunk, then fails — the worst case for
    a non-atomic replace (old chunks deleted, new ones half-written)."""

    def insert_chunks(self, conn, repository_id, chunks, embeddings, **kwargs):
        super().insert_chunks(conn, repository_id, chunks[:1], embeddings[:1], **kwargs)
        raise RuntimeError("simulated failure part-way through writing the new index")


def test_a_failed_reindex_preserves_the_previously_committed_index(tmp_path, vector_store):
    root = _write_repo(tmp_path / "repo", functions=("add", "sub"))
    repository_id = _service(vector_store).index_repository(str(root)).repository_id
    before = _chunk_rows(vector_store, repository_id)

    _write_repo(root, functions=("one", "two", "three"))
    failing = IndexingService(DeterministicLocalEmbeddingProvider(), _FailingAfterPartialInsertStore(vector_store._database_url))
    with pytest.raises(RuntimeError, match="simulated failure"):
        failing.index_repository(str(root))

    assert _chunk_rows(vector_store, repository_id) == before


def test_a_failed_first_index_leaves_no_repository_row(tmp_path, vector_store):
    root = _write_repo(tmp_path / "fresh")
    failing = IndexingService(DeterministicLocalEmbeddingProvider(), _FailingAfterPartialInsertStore(vector_store._database_url))

    with pytest.raises(RuntimeError):
        failing.index_repository(str(root))

    conn = vector_store.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM repositories WHERE root_path = %s", (str(root.resolve()),))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM code_chunks")
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()


def test_concurrent_reindexes_of_one_repository_do_not_duplicate_chunks(tmp_path, vector_store):
    """Two indexers racing on the same repository: the repository-row lock
    taken by the upsert must serialize their delete+insert, otherwise
    both could insert a full copy."""
    root = _write_repo(tmp_path / "repo", functions=tuple(f"fn_{i}" for i in range(40)))
    repository_id = _service(vector_store).index_repository(str(root)).repository_id
    errors = []

    def run():
        try:
            _service(vector_store).index_repository(str(root))
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == []
    rows = _chunk_rows(vector_store, repository_id)
    assert len(rows) == 40
    assert len(set(rows)) == 40
