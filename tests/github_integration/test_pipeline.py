"""Proves the Phase 11 pipeline composes cloning with the EXISTING
Phase 1/2/3 pipeline unchanged — a real network clone of a small public
repository, indexed via the same `IndexingService` every other phase's
tests already use. Skips cleanly if either api.github.com or the local
PostgreSQL test database isn't reachable.
"""

from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider

from github_integration.pipeline import ingest_github_repository

from tests.github_integration.conftest import PUBLIC_TEST_REPO_URL, requires_network
from tests.vectorstore.conftest import requires_postgres, vector_store


@requires_network
@requires_postgres
def test_ingest_github_repository_reuses_the_existing_pipeline_unchanged(tmp_path, vector_store):
    embedding_provider = DeterministicLocalEmbeddingProvider()

    result = ingest_github_repository(
        PUBLIC_TEST_REPO_URL, str(tmp_path), vector_store, embedding_provider
    )

    assert result.cloned.reference.owner == "octocat"
    assert result.ingestion_result.repository.total_files_discovered >= 1
    assert result.index_result.repository_id is not None
    # The cloned repo was actually the thing indexed, not a stale/other path.
    assert result.ingestion_result.repository.root_path == result.cloned.local_path
