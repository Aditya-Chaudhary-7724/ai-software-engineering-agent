"""Agent evaluation: task classification correctness, routing
correctness, absence of unnecessary capability invocations (see below
for how "tool selection" is honestly scoped here), bounded retry
behavior, and safe refusal pending human approval — run through the
REAL Phase 7/9/10 `AgentService` and its LangGraph workflow, never
reimplemented or simulated.

"Tool selection" is scoped honestly to this project's actual
architecture: there is no autonomous LLM-driven tool-calling loop in
this codebase (Phase 8 built a `ToolRegistry` with declarative schemas,
but the agent graph — Phase 7/9/10 — routes deterministically based on
`classify_task`, not an LLM choosing which tool to invoke). What this
runner evaluates instead is whether that deterministic routing invokes
only the capabilities appropriate to a task type, using the same
`execution_log` entries the agent already produces for observability —
e.g. an "answer" question must never produce a "Proposed a change" log
entry. This is a real, honestly-scoped stand-in for LLM
tool-selection evaluation, not a claim that autonomous tool selection
exists in this system.
"""

import tempfile
import time
import uuid
from pathlib import Path
from typing import List

from graph.builder import GraphBuilder
from graph.client import Neo4jClient
from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore

from rag.llm.stub_provider import StubLLMProvider

from sandbox.docker_runner import DockerTestRunner

from agent.service import AgentService
from agent.state import MAX_RETRIES

from evaluation.dataset import AGENT_CASES, AgentCase, build_sample_repository
from evaluation.models import CaseResult, MetricResult


def _cleanup(vector_store: VectorStore, repository_id: int, neo4j_client: Neo4jClient, root_path: str) -> None:
    conn = vector_store.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM code_chunks WHERE repository_id = %s", (repository_id,))
            cur.execute("DELETE FROM repositories WHERE id = %s", (repository_id,))
        conn.commit()
    finally:
        conn.close()
    neo4j_client.run("MATCH (n {repository_root_path: $root_path}) DETACH DELETE n", root_path=root_path)
    neo4j_client.run("MATCH (r:Repository {root_path: $root_path}) DETACH DELETE r", root_path=root_path)


def _evaluate_routing_case(case: AgentCase, service: AgentService, repository_id: int, root_path: str) -> CaseResult:
    start = time.monotonic()
    result = service.run(case.question, repository_id, root_path, thread_id=str(uuid.uuid4()))
    latency = time.monotonic() - start

    classification_correct = any(
        f"Classified task as '{case.expected_task_type}'" in e for e in result.execution_log
    )
    routing_correct = any(f"Decision: route to '{case.expected_decision}'" in e for e in result.execution_log)
    approval_gate_correct = (result.status == "awaiting_approval") == case.expect_awaiting_approval
    no_forbidden_calls = not any(
        forbidden in entry for entry in result.execution_log for forbidden in case.forbidden_log_substrings
    )

    metrics = [
        MetricResult(
            name="task_classification_correct", value=float(classification_correct), expected=1.0, passed=classification_correct
        ),
        MetricResult(name="routing_correct", value=float(routing_correct), expected=1.0, passed=routing_correct),
        MetricResult(
            name="approval_gate_correct", value=float(approval_gate_correct), expected=1.0, passed=approval_gate_correct
        ),
        MetricResult(
            name="no_unnecessary_capability_invoked", value=float(no_forbidden_calls), expected=1.0, passed=no_forbidden_calls
        ),
    ]
    passed = all(m.passed for m in metrics)

    return CaseResult(
        case_id=case.case_id,
        category="agent",
        description=case.description,
        passed=passed,
        metrics=metrics,
        latency_seconds=latency,
        execution_log=list(result.execution_log),
    )


def _evaluate_bounded_retry_case(service: AgentService, repository_id: int, root_path: str) -> CaseResult:
    """An empty repository forces repository_search to find nothing —
    code_analyzer must retry a bounded number of times (`MAX_RETRIES`),
    then honestly report no context was found: never loop forever, and
    never fabricate an answer when there is no evidence for one.
    """
    start = time.monotonic()
    result = service.run("What does this do?", repository_id, root_path, thread_id=str(uuid.uuid4()))
    latency = time.monotonic() - start

    retry_entries = [e for e in result.execution_log if "retrying" in e]
    bounded_correctly = len(retry_entries) == MAX_RETRIES
    honest_refusal = "No relevant code context was found" in (result.final_response or "")

    metrics = [
        MetricResult(
            name="retry_count", value=float(len(retry_entries)), expected=float(MAX_RETRIES), passed=bounded_correctly
        ),
        MetricResult(name="honest_refusal_on_no_context", value=float(honest_refusal), expected=1.0, passed=honest_refusal),
    ]
    passed = all(m.passed for m in metrics)

    return CaseResult(
        case_id="agent-bounded-retry",
        category="agent",
        description="An empty repository must trigger exactly MAX_RETRIES bounded retries, then an honest refusal.",
        passed=passed,
        metrics=metrics,
        latency_seconds=latency,
        execution_log=list(result.execution_log),
    )


def run_agent_evaluation(vector_store: VectorStore, neo4j_client: Neo4jClient) -> List[CaseResult]:
    embedding_provider = DeterministicLocalEmbeddingProvider()
    # Neither sample repo below has any test files, so DockerTestRunner
    # raises NoTestCommandError before Docker is ever invoked — this
    # runner doesn't need Docker reachable to evaluate routing/retries.
    test_runner = DockerTestRunner()

    results: List[CaseResult] = []

    with tempfile.TemporaryDirectory(prefix="eval-agent-") as tmp_dir:
        root = Path(tmp_dir)
        build_sample_repository(root)
        root_path = str(root.resolve())

        ingestion_result = IngestionService().ingest(str(root))
        parsing_result = ParsingService().parse_repository(str(root), ingestion_result)
        index_result = IndexingService(embedding_provider, vector_store).index_repository(str(root))
        GraphBuilder(neo4j_client).build(str(root), ingestion_result, parsing_result)

        service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), test_runner)
        try:
            for case in AGENT_CASES:
                results.append(_evaluate_routing_case(case, service, index_result.repository_id, root_path))
        finally:
            _cleanup(vector_store, index_result.repository_id, neo4j_client, root_path)

    with tempfile.TemporaryDirectory(prefix="eval-agent-empty-") as tmp_dir:
        root = Path(tmp_dir)
        root_path = str(root.resolve())

        ingestion_result = IngestionService().ingest(str(root))
        parsing_result = ParsingService().parse_repository(str(root), ingestion_result)
        index_result = IndexingService(embedding_provider, vector_store).index_repository(str(root))
        GraphBuilder(neo4j_client).build(str(root), ingestion_result, parsing_result)

        service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), test_runner)
        try:
            results.append(_evaluate_bounded_retry_case(service, index_result.repository_id, root_path))
        finally:
            _cleanup(vector_store, index_result.repository_id, neo4j_client, root_path)

    return results
