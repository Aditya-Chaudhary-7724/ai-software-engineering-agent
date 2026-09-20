"""Testing-loop evaluation: first-pass success, failure -> diagnosis ->
proposed fix -> approval -> rerun -> recovery, the bounded iteration
limit, the wall-clock timeout, honest failure after the maximum number
of retries, and that human approval cannot be bypassed on any fix
attempt — run through the REAL Phase 7/9/10 `AgentService` and its
LangGraph workflow.

Uses `evaluation.scripted_test_runner.ScriptedTestRunner` (a
deterministic, non-Docker stand-in — see its own docstring for why)
rather than a real Docker sandbox: this isolates the LOOP'S ROUTING
LOGIC (does it stop at the right time, does it always re-request
approval) from whether a stub LLM's placeholder text happens to pass or
fail a real test suite, which is a separate, already-covered concern
(see `evaluation/runners/modification.py`'s real-Docker case, and
`backend/scripts/manual_agent_demo.py`'s scenario 4 for the same loop
against a real container).
"""

import tempfile
import time
import uuid
from pathlib import Path
from typing import List

import agent.nodes as agent_nodes
from graph.builder import GraphBuilder
from graph.client import Neo4jClient
from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore

from rag.llm.stub_provider import StubLLMProvider

from agent.service import AgentService
from agent.state import MAX_FIX_ITERATIONS

from evaluation.dataset import build_sample_repository
from evaluation.models import CaseResult, MetricResult
from evaluation.scripted_test_runner import ScriptedTestRunner, make_result

_MODIFY_QUESTION = "Fix the greet function"


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


def _run_to_completion(service: AgentService, repository_id: int, root_path: str, max_rounds: int):
    """Drives run() -> resume(approved=True) until the graph reaches
    END or `max_rounds` approvals have been given (a safety cap on this
    *evaluation helper*, distinct from the agent's own MAX_FIX_ITERATIONS
    bound being evaluated).
    """
    thread_id = str(uuid.uuid4())
    result = service.run(_MODIFY_QUESTION, repository_id, root_path, thread_id)
    approvals = 0
    while result.status == "awaiting_approval" and approvals < max_rounds:
        approvals += 1
        result = service.resume(thread_id=thread_id, approved=True)
    return result, approvals


def _evaluate_first_pass_success(service_factory, index_result, root_path) -> CaseResult:
    start = time.monotonic()
    test_runner = ScriptedTestRunner([make_result(passed=True)])
    service = service_factory(test_runner)

    result, approvals = _run_to_completion(service, index_result.repository_id, root_path, max_rounds=5)

    success = "Tests: passed." in (result.final_response or "")
    exactly_one_approval = approvals == 1

    metrics = [
        MetricResult(name="tests_pass_on_first_try", value=float(success), expected=1.0, passed=success),
        MetricResult(name="approvals_needed", value=float(approvals), expected=1.0, passed=exactly_one_approval),
    ]
    return CaseResult(
        case_id="testing-loop-first-pass-success",
        category="testing_loop",
        description="A change whose tests pass immediately must complete after exactly one approval.",
        passed=all(m.passed for m in metrics),
        metrics=metrics,
        latency_seconds=time.monotonic() - start,
        execution_log=list(result.execution_log),
    )


def _evaluate_fail_then_recover(service_factory, index_result, root_path) -> CaseResult:
    start = time.monotonic()
    test_runner = ScriptedTestRunner([make_result(passed=False, stderr="AssertionError: boom"), make_result(passed=True)])
    service = service_factory(test_runner)

    result, approvals = _run_to_completion(service, index_result.repository_id, root_path, max_rounds=5)

    recovered = "Tests: passed." in (result.final_response or "")
    fix_attempt_logged = any("attempting fix 1/" in e for e in result.execution_log)
    exactly_two_approvals = approvals == 2

    metrics = [
        MetricResult(name="recovered_after_one_fix", value=float(recovered), expected=1.0, passed=recovered),
        MetricResult(name="fix_attempt_was_logged", value=float(fix_attempt_logged), expected=1.0, passed=fix_attempt_logged),
        MetricResult(name="approvals_needed", value=float(approvals), expected=2.0, passed=exactly_two_approvals),
    ]
    return CaseResult(
        case_id="testing-loop-fail-then-recover",
        category="testing_loop",
        description="A failing test suite must trigger one approved fix attempt, then succeed once tests pass.",
        passed=all(m.passed for m in metrics),
        metrics=metrics,
        latency_seconds=time.monotonic() - start,
        execution_log=list(result.execution_log),
    )


def _evaluate_iteration_limit(service_factory, index_result, root_path) -> CaseResult:
    start = time.monotonic()
    always_fails = [make_result(passed=False, stderr=f"AssertionError: attempt {i}") for i in range(MAX_FIX_ITERATIONS + 1)]
    test_runner = ScriptedTestRunner(always_fails)
    service = service_factory(test_runner)

    result, approvals = _run_to_completion(service, index_result.repository_id, root_path, max_rounds=MAX_FIX_ITERATIONS + 3)

    gave_up_honestly = f"Giving up after {MAX_FIX_ITERATIONS} fix attempt(s)" in (result.final_response or "")
    exact_approvals = approvals == MAX_FIX_ITERATIONS + 1
    exact_test_runs = test_runner.call_count == MAX_FIX_ITERATIONS + 1

    metrics = [
        MetricResult(name="gave_up_after_max_fix_iterations", value=float(gave_up_honestly), expected=1.0, passed=gave_up_honestly),
        MetricResult(name="approvals_bounded_correctly", value=float(approvals), expected=float(MAX_FIX_ITERATIONS + 1), passed=exact_approvals),
        MetricResult(name="test_runs_bounded_correctly", value=float(test_runner.call_count), expected=float(MAX_FIX_ITERATIONS + 1), passed=exact_test_runs),
    ]
    return CaseResult(
        case_id="testing-loop-iteration-limit",
        category="testing_loop",
        description=f"A test suite that never passes must stop after exactly MAX_FIX_ITERATIONS ({MAX_FIX_ITERATIONS}) fix attempts, never loop unboundedly.",
        passed=all(m.passed for m in metrics),
        metrics=metrics,
        latency_seconds=time.monotonic() - start,
        execution_log=list(result.execution_log),
    )


def _evaluate_wall_clock_timeout(service_factory, index_result, root_path) -> CaseResult:
    """Forces the wall-clock budget to 0 so the very first post-apply
    test failure already exceeds it — proving the time bound is
    enforced independently of the iteration-count bound (`fix_iteration`
    is still 0, well under `MAX_FIX_ITERATIONS`, when the loop stops).
    """
    start = time.monotonic()
    test_runner = ScriptedTestRunner([make_result(passed=False, stderr="boom")])
    service = service_factory(test_runner)

    original_budget = agent_nodes.MAX_LOOP_SECONDS
    agent_nodes.MAX_LOOP_SECONDS = 0
    try:
        result, approvals = _run_to_completion(service, index_result.repository_id, root_path, max_rounds=5)
    finally:
        agent_nodes.MAX_LOOP_SECONDS = original_budget

    stopped_on_time_budget = "time budget exhausted" in (result.final_response or "")
    only_one_approval = approvals == 1

    metrics = [
        MetricResult(name="stopped_due_to_time_budget", value=float(stopped_on_time_budget), expected=1.0, passed=stopped_on_time_budget),
        MetricResult(name="stopped_before_iteration_limit", value=float(only_one_approval), expected=1.0, passed=only_one_approval),
    ]
    return CaseResult(
        case_id="testing-loop-wall-clock-timeout",
        category="testing_loop",
        description="An exhausted wall-clock budget must stop the loop even when the iteration count is still well under MAX_FIX_ITERATIONS.",
        passed=all(m.passed for m in metrics),
        metrics=metrics,
        latency_seconds=time.monotonic() - start,
        execution_log=list(result.execution_log),
    )


def _evaluate_approval_cannot_be_bypassed(service_factory, index_result, root_path) -> CaseResult:
    start = time.monotonic()
    test_runner = ScriptedTestRunner([make_result(passed=False, stderr="boom")])
    service = service_factory(test_runner)

    thread_id = str(uuid.uuid4())
    result = service.run(_MODIFY_QUESTION, index_result.repository_id, root_path, thread_id)
    first_approval_gate = result.status == "awaiting_approval"

    result = service.resume(thread_id=thread_id, approved=True)  # approve the original change
    second_approval_gate = result.status == "awaiting_approval"  # now paused on the fix attempt

    result = service.resume(thread_id=thread_id, approved=False)  # reject the fix attempt
    stopped_immediately = result.status == "completed" and "not approved" in (result.final_response or "")
    fix_tests_never_ran = test_runner.call_count == 1  # only the original apply's test run happened

    metrics = [
        MetricResult(name="original_change_gated_by_approval", value=float(first_approval_gate), expected=1.0, passed=first_approval_gate),
        MetricResult(name="fix_attempt_gated_by_its_own_approval", value=float(second_approval_gate), expected=1.0, passed=second_approval_gate),
        MetricResult(name="rejecting_fix_stops_loop_immediately", value=float(stopped_immediately), expected=1.0, passed=stopped_immediately),
        MetricResult(name="fix_never_executed_without_approval", value=float(fix_tests_never_ran), expected=1.0, passed=fix_tests_never_ran),
    ]
    return CaseResult(
        case_id="testing-loop-approval-not-bypassable",
        category="testing_loop",
        description="Every fix attempt must be gated by its own human approval — rejecting one must stop the loop immediately.",
        passed=all(m.passed for m in metrics),
        metrics=metrics,
        latency_seconds=time.monotonic() - start,
        execution_log=list(result.execution_log),
    )


def run_testing_loop_evaluation(vector_store: VectorStore, neo4j_client: Neo4jClient) -> List[CaseResult]:
    embedding_provider = DeterministicLocalEmbeddingProvider()
    results: List[CaseResult] = []

    case_builders = [
        _evaluate_first_pass_success,
        _evaluate_fail_then_recover,
        _evaluate_iteration_limit,
        _evaluate_wall_clock_timeout,
        _evaluate_approval_cannot_be_bypassed,
    ]

    for builder in case_builders:
        with tempfile.TemporaryDirectory(prefix="eval-testing-loop-") as tmp_dir:
            root = Path(tmp_dir)
            build_sample_repository(root)  # only greet.py's content matters; no test file needed (scripted runner)
            root_path = str(root.resolve())

            ingestion_result = IngestionService().ingest(str(root))
            parsing_result = ParsingService().parse_repository(str(root), ingestion_result)
            index_result = IndexingService(embedding_provider, vector_store).index_repository(str(root))
            GraphBuilder(neo4j_client).build(str(root), ingestion_result, parsing_result)

            def service_factory(test_runner):
                return AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), test_runner)

            try:
                results.append(builder(service_factory, index_result, root_path))
            finally:
                _cleanup(vector_store, index_result.repository_id, neo4j_client, root_path)

    return results
