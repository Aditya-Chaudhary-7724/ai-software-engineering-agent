"""Code modification security (Phase 14, section 6): adversarial
regression tests for approval-bypass attempts, against the REAL
`ModificationService` and (for the retry/resume path) the REAL
`AgentService`/LangGraph workflow — not mocks. Covers:

1. A caller that constructs `ModificationService` directly, bypassing
   the agent graph entirely, cannot apply a change without explicitly
   asserting `approved=True` (the Phase 14 hardening this phase added
   — see modification/service.py and docs/security.md).
2. Stale-change protection still holds (Phase 9, re-verified here).
3. Path traversal cannot select an arbitrary file to modify.
4. Rejecting a fix-loop retry stops the loop immediately — approval
   cannot be bypassed via retries, even indirectly (Phase 10).
"""

import uuid

import pytest

from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService

from graph.builder import GraphBuilder

from modification.exceptions import StaleChangeError, UnauthorizedChangeError
from modification.models import ProposedChange
from modification.service import ModificationService

from rag.llm.stub_provider import StubLLMProvider

from sandbox.docker_runner import DockerTestRunner

from agent.service import AgentService

from tools.exceptions import ToolAuthorizationError

from tests.security.conftest import neo4j_client, requires_neo4j, requires_postgres, vector_store


@requires_postgres
def test_apply_change_refuses_without_explicit_approval(tmp_path, vector_store):
    """The direct-service-call bypass this phase closes: constructing
    ModificationService and calling apply_change with NO approval
    signal at all must fail closed, not apply the change.
    """
    original_content = "def greet():\n    return 'hi'\n"
    (tmp_path / "main.py").write_text(original_content)

    ingestion_result = IngestionService().ingest(str(tmp_path))
    ParsingService().parse_repository(str(tmp_path), ingestion_result)
    embedding_provider = DeterministicLocalEmbeddingProvider()
    index_result = IndexingService(embedding_provider, vector_store).index_repository(str(tmp_path))

    try:
        service = ModificationService(vector_store, embedding_provider, StubLLMProvider())
        proposal = service.propose_change(str(tmp_path), index_result.repository_id, "fix greet")

        with pytest.raises(UnauthorizedChangeError):
            service.apply_change(str(tmp_path), proposal, approved=False)

        assert (tmp_path / "main.py").read_text() == original_content  # untouched
    finally:
        conn = vector_store.connect()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM code_chunks WHERE repository_id = %s", (index_result.repository_id,))
            cur.execute("DELETE FROM repositories WHERE id = %s", (index_result.repository_id,))
        conn.commit()
        conn.close()


def test_apply_change_signature_requires_approved_as_a_keyword_argument():
    """A caller cannot accidentally satisfy the approval requirement by
    passing a positional truthy value meant for something else —
    `approved` is keyword-only.
    """
    import inspect

    signature = inspect.signature(ModificationService.apply_change)
    approved_param = signature.parameters["approved"]
    assert approved_param.kind == inspect.Parameter.KEYWORD_ONLY
    assert approved_param.default is inspect.Parameter.empty  # no default — must be supplied explicitly


@requires_postgres
def test_stale_change_is_refused_even_if_the_caller_claims_approval(tmp_path, vector_store):
    """Approval is necessary but not sufficient: even a genuinely
    approved change must still be refused if the file changed since
    the proposal was generated (Phase 9, re-verified as a Phase 14
    regression).
    """
    original_content = "def greet():\n    return 'hi'\n"
    (tmp_path / "main.py").write_text(original_content)

    ingestion_result = IngestionService().ingest(str(tmp_path))
    ParsingService().parse_repository(str(tmp_path), ingestion_result)
    embedding_provider = DeterministicLocalEmbeddingProvider()
    index_result = IndexingService(embedding_provider, vector_store).index_repository(str(tmp_path))

    try:
        service = ModificationService(vector_store, embedding_provider, StubLLMProvider())
        proposal = service.propose_change(str(tmp_path), index_result.repository_id, "fix greet")

        concurrent_edit = "def greet():\n    return 'edited concurrently'\n"
        (tmp_path / "main.py").write_text(concurrent_edit)

        with pytest.raises(StaleChangeError):
            service.apply_change(str(tmp_path), proposal, approved=True)

        assert (tmp_path / "main.py").read_text() == concurrent_edit
    finally:
        conn = vector_store.connect()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM code_chunks WHERE repository_id = %s", (index_result.repository_id,))
            cur.execute("DELETE FROM repositories WHERE id = %s", (index_result.repository_id,))
        conn.commit()
        conn.close()


def test_apply_change_cannot_be_pointed_at_an_arbitrary_file_via_path_traversal(tmp_path):
    """A malicious/malformed ProposedChange.relative_path (however it
    was produced) cannot select a file outside the repository root —
    approval does not override the path-safety boundary. `apply_change`
    never touches `vector_store`/`embedding_provider` (only
    `propose_change` does), so `None` stand-ins are safe here.
    """
    service = ModificationService(None, None, StubLLMProvider())
    malicious_proposal = ProposedChange(
        relative_path="../../../etc/passwd", original_content="", proposed_content="pwned", diff=""
    )

    with pytest.raises(ToolAuthorizationError):
        service.apply_change(str(tmp_path), malicious_proposal, approved=True)


@requires_postgres
@requires_neo4j
def test_rejecting_a_fix_loop_retry_stops_the_agent_immediately_no_further_changes(tmp_path, vector_store, neo4j_client):
    """Approval cannot be bypassed through the fix-loop's retry/resume
    path (Phase 10): rejecting the SECOND approval (a fix attempt) must
    stop the loop immediately, never silently continuing or applying
    the pending fix anyway.
    """
    from evaluation.scripted_test_runner import ScriptedTestRunner, make_result

    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    embedding_provider = DeterministicLocalEmbeddingProvider()
    index_result = IndexingService(embedding_provider, vector_store).index_repository(str(tmp_path))
    GraphBuilder(neo4j_client).build(str(tmp_path), ingestion_result, parsing_result)
    root_path = str(tmp_path.resolve())

    try:
        test_runner = ScriptedTestRunner([make_result(passed=False, stderr="boom")])
        service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), test_runner)
        thread_id = str(uuid.uuid4())

        result = service.run("Fix the greet function", index_result.repository_id, root_path, thread_id)
        assert result.status == "awaiting_approval"
        result = service.resume(thread_id, approved=True)  # approve the original change
        assert result.status == "awaiting_approval"  # now paused on the fix attempt

        result = service.resume(thread_id, approved=False)  # REJECT the fix

        assert result.status == "completed"
        assert "not approved" in result.final_response
        assert test_runner.call_count == 1  # the fix's tests were never run
    finally:
        conn = vector_store.connect()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM code_chunks WHERE repository_id = %s", (index_result.repository_id,))
            cur.execute("DELETE FROM repositories WHERE id = %s", (index_result.repository_id,))
        conn.commit()
        conn.close()
        neo4j_client.run("MATCH (n {repository_root_path: $root_path}) DETACH DELETE n", root_path=root_path)
        neo4j_client.run("MATCH (r:Repository {root_path: $root_path}) DETACH DELETE r", root_path=root_path)
