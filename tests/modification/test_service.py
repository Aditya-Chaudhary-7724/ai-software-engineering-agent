"""LOCAL TESTED against real PostgreSQL + pgvector (for find_affected_file);
skips cleanly if unreachable. Uses isolated tmp_path fixtures throughout —
never the real project repository — per this phase's own testing
requirement.
"""

import pytest

from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService

from modification.exceptions import ModificationError, StaleChangeError
from modification.models import ProposedChange
from modification.service import ModificationService
from rag.llm.stub_provider import StubLLMProvider
from tools.exceptions import ToolAuthorizationError

from tests.modification.conftest import requires_postgres, vector_store


def _index(tmp_path, vector_store):
    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    embedding_provider = DeterministicLocalEmbeddingProvider()
    index_result = IndexingService(embedding_provider, vector_store).index_repository(str(tmp_path))
    return index_result, embedding_provider


@requires_postgres
def test_propose_change_finds_file_and_generates_diff(tmp_path, vector_store):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index(tmp_path, vector_store)

    service = ModificationService(vector_store, embedding_provider, StubLLMProvider())
    proposal = service.propose_change(str(tmp_path), index_result.repository_id, "fix greet")

    assert proposal.relative_path == "main.py"
    assert proposal.original_content == "def greet():\n    return 'hi'\n"
    assert "stub-llm" in proposal.proposed_content
    assert proposal.diff != ""


@requires_postgres
def test_propose_change_no_relevant_file_raises(tmp_path, vector_store):
    index_result, embedding_provider = _index(tmp_path, vector_store)

    service = ModificationService(vector_store, embedding_provider, StubLLMProvider())
    with pytest.raises(ModificationError):
        service.propose_change(str(tmp_path), index_result.repository_id, "fix the bug")


@requires_postgres
def test_apply_change_writes_proposed_content(tmp_path, vector_store):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index(tmp_path, vector_store)

    service = ModificationService(vector_store, embedding_provider, StubLLMProvider())
    proposal = service.propose_change(str(tmp_path), index_result.repository_id, "fix greet")
    result = service.apply_change(str(tmp_path), proposal, approved=True)

    assert result.applied is True
    assert (tmp_path / "main.py").read_text() == proposal.proposed_content


@requires_postgres
def test_apply_change_refuses_stale_proposal(tmp_path, vector_store):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index(tmp_path, vector_store)

    service = ModificationService(vector_store, embedding_provider, StubLLMProvider())
    proposal = service.propose_change(str(tmp_path), index_result.repository_id, "fix greet")

    (tmp_path / "main.py").write_text("def greet():\n    return 'edited by someone else'\n")

    with pytest.raises(StaleChangeError):
        service.apply_change(str(tmp_path), proposal, approved=True)

    # the concurrent edit must survive untouched
    assert (tmp_path / "main.py").read_text() == "def greet():\n    return 'edited by someone else'\n"


@requires_postgres
def test_apply_change_rejects_path_traversal(tmp_path, vector_store):
    index_result, embedding_provider = _index(tmp_path, vector_store)
    service = ModificationService(vector_store, embedding_provider, StubLLMProvider())

    malicious_proposal = ProposedChange(
        relative_path="../outside.py", original_content="", proposed_content="x = 1\n", diff=""
    )

    with pytest.raises(ToolAuthorizationError):
        service.apply_change(str(tmp_path), malicious_proposal, approved=True)
