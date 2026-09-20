"""Node implementations for the agent workflow.

Nodes that need a service (vector store, Neo4j client, embedding
provider, LLM provider) are built via factory functions that close
over those services — LangGraph node functions only receive state, so
dependency injection happens at graph-construction time (see graph.py).

Every node's `execution_log` entry is a short, safe summary of what it
did (e.g. "Searching repository... found 3 candidate(s)") — never the
model's raw reasoning or hidden chain-of-thought.
"""

import dataclasses
import time
from types import SimpleNamespace

from langgraph.types import interrupt

from graph.client import Neo4jClient
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.store import VectorStore

from rag.context import build_context
from rag.keyword_search import keyword_search
from rag.llm.base import LLMProvider
from rag.prompt import SYSTEM_PROMPT, build_prompt
from rag.ranking import merge_and_rank as merge_seed_candidates

from hybrid.graph_expansion import build_graph_candidates
from hybrid.ranking import merge_and_rank as merge_hybrid_candidates

from modification.exceptions import ModificationError
from modification.models import ProposedChange
from modification.service import ModificationService

from sandbox.base import TestRunner
from sandbox.exceptions import DockerUnavailableError, NoTestCommandError
from sandbox.models import TestRunResult

from agent.classification import classify_task
from agent.state import DEFAULT_FINAL_TOP_K, MAX_FIX_ITERATIONS, MAX_LOOP_SECONDS, MAX_RETRIES, AgentState

# Bounds how much raw test output is echoed back into a fix instruction or a
# final response — the same "bounded context" principle as rag/context.py,
# applied to sandbox output instead of retrieved code.
_MAX_TEST_OUTPUT_CHARS = 2000


def _truncate(text: str, limit: int = _MAX_TEST_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more characters]"


def task_analyzer_node(state: AgentState) -> dict:
    task_type = classify_task(state["question"])
    return {"task_type": task_type, "execution_log": [f"Classified task as '{task_type}'."]}


def planner_node(state: AgentState) -> dict:
    task_type = state["task_type"]
    if task_type == "answer":
        plan = "Retrieve relevant code context, then generate a grounded answer."
    elif task_type == "modify":
        plan = "A code change is requested; this requires human approval before any modification is attempted."
    else:
        plan = "Test execution is requested."
    return {"plan": plan, "execution_log": [f"Plan: {plan}"]}


def make_repository_search_node(vector_store: VectorStore, embedding_provider: EmbeddingProvider):
    def repository_search_node(state: AgentState) -> dict:
        conn = vector_store.connect()
        try:
            query_embedding = embedding_provider.embed([state["question"]])[0]
            vector_hits = vector_store.similarity_search(
                conn, query_embedding, top_k=state["vector_top_k"], repository_id=state["repository_id"]
            )
            keyword_hits = keyword_search(
                conn, state["question"], top_k=state["keyword_top_k"], repository_id=state["repository_id"]
            )
        finally:
            conn.close()

        seeds = merge_seed_candidates(vector_hits, keyword_hits, top_k=state["seed_limit"])

        return {
            "vector_hits": [dataclasses.asdict(h) for h in vector_hits],
            "keyword_hits": [dataclasses.asdict(h) for h in keyword_hits],
            "seed_candidates": [dataclasses.asdict(s) for s in seeds],
            "execution_log": [
                f"Searching repository... {len(vector_hits)} vector match(es), "
                f"{len(keyword_hits)} keyword match(es)."
            ],
        }

    return repository_search_node


def make_graph_search_node(neo4j_client: Neo4jClient, vector_store: VectorStore):
    def graph_search_node(state: AgentState) -> dict:
        seeds = [SimpleNamespace(**d) for d in state["seed_candidates"]]
        conn = vector_store.connect()
        try:
            graph_hits = build_graph_candidates(
                neo4j_client, conn, state["repository_id"], state["root_path"], seeds
            )
        finally:
            conn.close()

        return {
            "graph_candidates": [dataclasses.asdict(g) for g in graph_hits],
            "execution_log": [f"Analyzing dependencies... found {len(graph_hits)} related symbol(s)."],
        }

    return graph_search_node


def code_analyzer_node(state: AgentState) -> dict:
    vector_hits = [SimpleNamespace(**d) for d in state["vector_hits"]]
    keyword_hits = [SimpleNamespace(**d) for d in state["keyword_hits"]]
    graph_hits = [SimpleNamespace(**d) for d in state["graph_candidates"]]

    # SimpleNamespace duck-types as SearchResult/Candidate/HybridCandidate here
    # (same attributes, reconstructed from the plain-dict state — see state.py's
    # docstring on why state holds dicts rather than these dataclasses directly).
    ranked = merge_hybrid_candidates(vector_hits, keyword_hits, graph_hits, top_k=DEFAULT_FINAL_TOP_K)  # type: ignore[arg-type]

    distinct_files = sorted({r.relative_path for r in ranked})
    summary = f"Found {len(ranked)} relevant chunk(s) across {len(distinct_files)} file(s)."

    result: dict = {
        "ranked_candidates": [dataclasses.asdict(r) for r in ranked],
        "analysis_summary": summary,
        "execution_log": [summary],
    }

    should_retry = not ranked and state["retry_count"] < MAX_RETRIES
    result["should_retry"] = should_retry
    if should_retry:
        result["retry_count"] = state["retry_count"] + 1
        result["vector_top_k"] = state["vector_top_k"] * 2
        result["keyword_top_k"] = state["keyword_top_k"] * 2
        result["execution_log"].append(
            f"No results found; retrying with a broader search (attempt {result['retry_count']}/{MAX_RETRIES})."
        )

    return result


def decision_node(state: AgentState) -> dict:
    task_type = state["task_type"]
    decision = "modify" if task_type == "modify" else task_type or "answer"
    return {"decision": decision, "execution_log": [f"Decision: route to '{decision}'."]}


def make_answer_node(llm_provider: LLMProvider):
    def answer_node(state: AgentState) -> dict:
        ranked = [SimpleNamespace(**d) for d in state["ranked_candidates"]]
        context_text, included = build_context(ranked)
        prompt = build_prompt(state["question"], context_text)
        answer_text = llm_provider.generate(prompt, system=SYSTEM_PROMPT)

        sources = [
            {
                "relative_path": c.relative_path,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "chunk_type": c.chunk_type,
                "symbol_name": c.symbol_name,
                "found_via": list(c.found_via),
            }
            for c in included
        ]

        return {
            "final_response": answer_text,
            "sources": sources,
            "execution_log": ["Generated grounded answer from retrieved context."],
        }

    return answer_node


def _test_result_fields(result: TestRunResult) -> dict:
    return {
        "test_ran": True,
        "test_passed": result.passed,
        "test_command": result.command,
        "test_stdout": result.stdout,
        "test_stderr": result.stderr,
        "test_exit_code": result.exit_code,
        "test_timed_out": result.timed_out,
    }


def _test_summary_word(result: TestRunResult) -> str:
    if result.timed_out:
        return "timed out"
    return "passed" if result.passed else "failed"


def make_test_node(test_runner: TestRunner):
    def test_node(state: AgentState) -> dict:
        try:
            result = test_runner.run(state["root_path"])
        except NoTestCommandError:
            message = (
                "No supported test command was detected for this repository. Automated test "
                "execution currently supports Python repositories using pytest (Phase 10)."
            )
            return {
                "final_response": message,
                "sources": [],
                "execution_log": ["Test execution requested but no supported test command was detected."],
            }
        except DockerUnavailableError as exc:
            return {
                "final_response": f"Could not run tests: {exc}",
                "sources": [],
                "execution_log": [f"Test execution requested but the sandbox is unavailable: {exc}"],
            }

        summary = _test_summary_word(result)
        message = (
            f"Tests {summary} (exit code {result.exit_code}, {result.duration_seconds:.1f}s).\n\n"
            f"stdout:\n{_truncate(result.stdout)}\n\nstderr:\n{_truncate(result.stderr)}"
        )
        return {
            **_test_result_fields(result),
            "final_response": message,
            "sources": [],
            "execution_log": [
                f"Running tests in sandbox... {summary} (exit_code={result.exit_code}, "
                f"{result.duration_seconds:.1f}s)."
            ],
        }

    return test_node


def make_propose_change_node(
    vector_store: VectorStore, embedding_provider: EmbeddingProvider, llm_provider: LLMProvider
):
    def propose_change_node(state: AgentState) -> dict:
        # On the first pass this is None, so the original question drives the
        # proposal. On a fix-loop retry (Phase 10), run_tests_after_apply_node
        # already overwrote this with an instruction built from the failing
        # test output, so this proposal targets the actual failure.
        instruction = state["modification_instruction"] or state["question"]

        service = ModificationService(vector_store, embedding_provider, llm_provider)
        try:
            proposal = service.propose_change(state["root_path"], state["repository_id"], instruction)
        except ModificationError as exc:
            return {
                "has_proposal": False,
                "modification_instruction": instruction,
                "final_response": str(exc),
                "sources": [],
                "execution_log": [f"Could not propose a change: {exc}"],
            }

        return {
            "has_proposal": True,
            "modification_instruction": instruction,
            "proposed_relative_path": proposal.relative_path,
            "proposed_original_content": proposal.original_content,
            "proposed_content": proposal.proposed_content,
            "proposed_diff": proposal.diff,
            "execution_log": [f"Proposed a change to '{proposal.relative_path}'; awaiting human approval."],
        }

    return propose_change_node


def human_approval_node(state: AgentState) -> dict:
    # On a Phase 10 fix-loop retry, `modification_instruction` has been
    # overwritten with a fix instruction built from the failing test output
    # (see run_tests_after_apply_node) — shown here so the reviewer approves
    # what will actually be generated, not the original request.
    is_fix_attempt = state["fix_iteration"] > 0
    decision = interrupt(
        {
            "message": (
                f"Fix attempt {state['fix_iteration']}/{MAX_FIX_ITERATIONS} requires human approval "
                "before it is applied."
                if is_fix_attempt
                else "This change requires human approval before it is applied."
            ),
            "question": state["question"],
            "instruction": state["modification_instruction"] or state["question"],
            "relative_path": state["proposed_relative_path"],
            "diff": state["proposed_diff"],
        }
    )
    approved = bool(decision)
    return {"approved": approved, "execution_log": [f"Human approval: {'approved' if approved else 'rejected'}."]}


def make_apply_change_node(vector_store: VectorStore, embedding_provider: EmbeddingProvider, llm_provider: LLMProvider):
    def apply_change_node(state: AgentState) -> dict:
        if not state.get("approved"):
            return {
                "applied": False,
                "final_response": "Modification request was not approved by a human reviewer. No changes were made.",
                "sources": [],
                "execution_log": ["Modification rejected; no changes made."],
            }

        # Only reachable via propose_change -> human_approval, which sets
        # all four of these together (has_proposal=True) — never partially.
        assert state["proposed_relative_path"] is not None
        assert state["proposed_original_content"] is not None
        assert state["proposed_content"] is not None
        assert state["proposed_diff"] is not None

        service = ModificationService(vector_store, embedding_provider, llm_provider)
        proposal = ProposedChange(
            relative_path=state["proposed_relative_path"],
            original_content=state["proposed_original_content"],
            proposed_content=state["proposed_content"],
            diff=state["proposed_diff"],
        )

        try:
            # `approved=True` is safe here specifically: it's only ever
            # reached after the `state.get("approved")` check above, which
            # is itself only ever true after a real LangGraph interrupt
            # resumed with an explicit human decision — never inferred.
            result = service.apply_change(state["root_path"], proposal, approved=True)
        except ModificationError as exc:
            # Approved, but nothing was actually written — do not proceed to
            # test verification (route_after_apply reads `applied`, not
            # `approved`, precisely to keep this case out of the test loop).
            return {
                "applied": False,
                "final_response": f"Approved, but the change could not be applied: {exc}",
                "sources": [],
                "execution_log": [f"Apply failed: {exc}"],
            }

        message = f"{result.message}\n\nDiff applied:\n{result.diff}"
        return {
            "applied": True,
            "final_response": message,
            "sources": [{"relative_path": result.relative_path, "found_via": ["modification"]}],
            "execution_log": [result.message],
        }

    return apply_change_node


def make_run_tests_after_apply_node(test_runner: TestRunner):
    """Phase 10: verifies an applied change by running the repository's
    test suite in the sandbox, then decides whether to attempt a bounded,
    human-approved fix.

    Only reachable when apply_change_node actually wrote a change
    (`_route_after_apply` in graph.py routes here only if `applied` is
    True) — a rejected or failed apply never reaches this node, so no
    test run is ever wasted verifying a change that was never made.
    """

    def run_tests_after_apply_node(state: AgentState) -> dict:
        deadline = state["loop_deadline"] or (time.monotonic() + MAX_LOOP_SECONDS)

        try:
            result = test_runner.run(state["root_path"])
        except NoTestCommandError:
            return {
                "test_ran": False,
                "should_retry_fix": False,
                "loop_deadline": deadline,
                "final_response": (
                    f"{state['final_response']}\n\nTests: no supported test command was detected "
                    "for this repository; the change was applied but not verified."
                ),
                "execution_log": ["No supported test command detected; skipping test verification."],
            }
        except DockerUnavailableError as exc:
            return {
                "test_ran": False,
                "should_retry_fix": False,
                "loop_deadline": deadline,
                "final_response": f"{state['final_response']}\n\nTests: could not run ({exc}).",
                "execution_log": [f"Sandbox unavailable: {exc}"],
            }

        summary = _test_summary_word(result)
        log_entry = (
            f"Running tests in sandbox... {summary} (exit_code={result.exit_code}, "
            f"{result.duration_seconds:.1f}s)."
        )
        base_update = {**_test_result_fields(result), "loop_deadline": deadline}

        if result.passed:
            return {
                **base_update,
                "should_retry_fix": False,
                "final_response": f"{state['final_response']}\n\nTests: passed.",
                "execution_log": [log_entry],
            }

        time_left = deadline - time.monotonic()
        can_retry = state["fix_iteration"] < MAX_FIX_ITERATIONS and time_left > 0
        if not can_retry:
            reason = "fix-attempt limit reached" if state["fix_iteration"] >= MAX_FIX_ITERATIONS else "time budget exhausted"
            return {
                **base_update,
                "should_retry_fix": False,
                "final_response": (
                    f"{state['final_response']}\n\nTests: {summary}. Giving up after "
                    f"{state['fix_iteration']} fix attempt(s) ({reason}).\n\n"
                    f"Last test output (stderr):\n{_truncate(result.stderr)}"
                ),
                "execution_log": [log_entry, f"Stopping fix loop: {reason}."],
            }

        next_iteration = state["fix_iteration"] + 1
        fix_instruction = (
            f"The previous change to '{state['proposed_relative_path']}' caused the test suite to "
            f"fail. Fix the code so the tests pass.\n\nTest output (stdout):\n{_truncate(result.stdout)}"
            f"\n\nTest output (stderr):\n{_truncate(result.stderr)}"
        )
        return {
            **base_update,
            "should_retry_fix": True,
            "fix_iteration": next_iteration,
            "modification_instruction": fix_instruction,
            "execution_log": [
                log_entry,
                f"Tests failed; attempting fix {next_iteration}/{MAX_FIX_ITERATIONS} "
                "(requires new human approval).",
            ],
        }

    return run_tests_after_apply_node
