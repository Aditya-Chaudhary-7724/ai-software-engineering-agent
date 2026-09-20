"""Builds the compiled LangGraph workflow.

Topology (matches the project's specified agent design; Phase 9 wired
in the real modify path replacing Phase 7's stub, and Phase 10 wired in
the real testing loop replacing Phase 9's "Tests: not run" stub):

    START -> task_analyzer -> planner -> repository_search -> graph_search
          -> code_analyzer -[retry?]-> (back to repository_search, bounded by MAX_RETRIES)
          -> decision -> answer -> END
                       -> test (real: Phase 10 sandbox) -> END
                       -> propose_change -[no relevant file found?]-> END
                                          -> human_approval -> apply_change -[rejected/failed?]-> END
                                                                            -[applied?]-> run_tests_after_apply
                                                                                          -[passed, or gave up?]-> END
                                                                                          -[failed, can retry?]-> propose_change
                                                                                             (loops back; a fresh
                                                                                              human_approval interrupt
                                                                                              gates every retry too)

"modify" means: generate a diff first, THEN ask for approval — matching
this phase's own spec ("Show diff -> HUMAN APPROVAL -> Apply"), not
Phase 7's earlier "approve blindly, then reveal a stub" order. The
approval gate is a real LangGraph `interrupt()` (see nodes.py);
apply_change is the only place in this project that writes to a
repository file, and only ever runs after that interrupt resumes
`approved=True`.

Retries are bounded structurally in two independent loops:
- code_analyzer only loops back to repository_search when it just set
  `should_retry` itself (fresh every call, never stale), bounded by
  `retry_count < MAX_RETRIES`.
- run_tests_after_apply only loops back to propose_change when it just
  set `should_retry_fix` itself, bounded by BOTH `fix_iteration <
  MAX_FIX_ITERATIONS` and a wall-clock `loop_deadline` — see
  agent/state.py and agent/nodes.py. Every one of those retries still
  goes through propose_change -> human_approval -> apply_change, so the
  human-approval gate is never bypassed no matter how many fix attempts
  are made, and the agent can never make an unbounded number of
  unsupervised changes.
"""

from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from graph.client import Neo4jClient
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.store import VectorStore

from rag.llm.base import LLMProvider

from sandbox.base import TestRunner

from observability.node_tracing import traced_node
from observability.recorder import NullRecorder
from observability.tracer import Tracer

from agent.nodes import (
    code_analyzer_node,
    decision_node,
    human_approval_node,
    make_answer_node,
    make_apply_change_node,
    make_graph_search_node,
    make_propose_change_node,
    make_repository_search_node,
    make_run_tests_after_apply_node,
    make_test_node,
    planner_node,
    task_analyzer_node,
)
from agent.state import AgentState


def _route_after_analysis(state: AgentState) -> str:
    return "retry" if state["should_retry"] else "continue"


def _route_decision(state: AgentState) -> str:
    return state["decision"] or "answer"


def _route_after_proposal(state: AgentState) -> str:
    return "propose_succeeded" if state["has_proposal"] else "propose_failed"


def _route_after_apply(state: AgentState) -> str:
    # Reads `applied`, not `approved`: an approved change can still fail to
    # apply (e.g. StaleChangeError), and that case must not enter the test
    # loop either — see apply_change_node's docstring in nodes.py.
    return "verify" if state["applied"] else "done"


def _route_after_test_check(state: AgentState) -> str:
    return "retry_fix" if state["should_retry_fix"] else "done"


def build_agent_graph(
    vector_store: VectorStore,
    neo4j_client: Neo4jClient,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
    test_runner: TestRunner,
    tracer: Optional[Tracer] = None,
    checkpointer: Optional[BaseCheckpointSaver] = None,
):
    """Phase 13: every node is wrapped with a tracing span here, in the
    wiring layer — `agent/nodes.py`'s own function bodies are untouched.
    Each `extract` callback pulls a small, already-safe subset of a
    node's OWN return dict (counts, decisions, booleans already present
    in `AgentState` — never a diff, stdout/stderr, or full file content)
    into that node's span attributes. `tracer` defaults to a
    `Tracer(NullRecorder())` so nothing behaves differently when a
    caller (or an existing test) doesn't supply one.
    """
    tracer = tracer or Tracer(NullRecorder())
    workflow = StateGraph(AgentState)

    def add_traced_node(name: str, kind: str, fn, extract=None) -> None:
        # mypy cannot resolve `StateGraph.add_node`'s overloaded, Protocol-based
        # stub against a callable produced by a generic decorator (`traced_node`)
        # — a known class of mypy/decorator friction, not a real type error: the
        # wrapped node has the exact same (state: AgentState) -> dict shape as
        # the original (verified by the full test suite and real end-to-end
        # traces — see tests/observability/test_agent_integration.py).
        workflow.add_node(name, traced_node(tracer, name, kind, extract=extract)(fn))  # type: ignore[call-overload]

    add_traced_node("task_analyzer", "node", task_analyzer_node, extract=lambda r: {"task_type": r.get("task_type")})
    add_traced_node("planner", "node", planner_node, extract=lambda r: {"plan": r.get("plan")})
    add_traced_node(
        "repository_search",
        "retrieval",
        make_repository_search_node(vector_store, embedding_provider),
        extract=lambda r: {
            "vector_hit_count": len(r.get("vector_hits", [])),
            "keyword_hit_count": len(r.get("keyword_hits", [])),
        },
    )
    add_traced_node(
        "graph_search",
        "graph",
        make_graph_search_node(neo4j_client, vector_store),
        extract=lambda r: {"graph_candidate_count": len(r.get("graph_candidates", []))},
    )
    add_traced_node(
        "code_analyzer",
        "node",
        code_analyzer_node,
        extract=lambda r: {
            "ranked_candidate_count": len(r.get("ranked_candidates", [])),
            "should_retry": r.get("should_retry"),
            "retry_count": r.get("retry_count"),
        },
    )
    add_traced_node("decision", "node", decision_node, extract=lambda r: {"decision": r.get("decision")})
    add_traced_node(
        "answer", "node", make_answer_node(llm_provider), extract=lambda r: {"source_count": len(r.get("sources", []))}
    )
    add_traced_node(
        "test",
        "sandbox",
        make_test_node(test_runner),
        extract=lambda r: {
            "test_ran": r.get("test_ran"),
            "test_passed": r.get("test_passed"),
            "test_exit_code": r.get("test_exit_code"),
            "test_timed_out": r.get("test_timed_out"),
        },
    )
    add_traced_node(
        "propose_change",
        "modification",
        make_propose_change_node(vector_store, embedding_provider, llm_provider),
        extract=lambda r: {"has_proposal": r.get("has_proposal"), "proposed_relative_path": r.get("proposed_relative_path")},
    )
    add_traced_node("human_approval", "approval", human_approval_node, extract=lambda r: {"approved": r.get("approved")})
    add_traced_node(
        "apply_change",
        "modification",
        make_apply_change_node(vector_store, embedding_provider, llm_provider),
        extract=lambda r: {"applied": r.get("applied")},
    )
    add_traced_node(
        "run_tests_after_apply",
        "sandbox",
        make_run_tests_after_apply_node(test_runner),
        extract=lambda r: {
            "test_ran": r.get("test_ran"),
            "test_passed": r.get("test_passed"),
            "fix_iteration": r.get("fix_iteration"),
            "should_retry_fix": r.get("should_retry_fix"),
        },
    )

    workflow.add_edge(START, "task_analyzer")
    workflow.add_edge("task_analyzer", "planner")
    workflow.add_edge("planner", "repository_search")
    workflow.add_edge("repository_search", "graph_search")
    workflow.add_edge("graph_search", "code_analyzer")
    workflow.add_conditional_edges(
        "code_analyzer", _route_after_analysis, {"retry": "repository_search", "continue": "decision"}
    )
    workflow.add_conditional_edges(
        "decision", _route_decision, {"answer": "answer", "test": "test", "modify": "propose_change"}
    )
    workflow.add_conditional_edges(
        "propose_change", _route_after_proposal, {"propose_succeeded": "human_approval", "propose_failed": END}
    )
    workflow.add_edge("human_approval", "apply_change")
    workflow.add_conditional_edges("apply_change", _route_after_apply, {"verify": "run_tests_after_apply", "done": END})
    workflow.add_conditional_edges(
        "run_tests_after_apply", _route_after_test_check, {"retry_fix": "propose_change", "done": END}
    )
    workflow.add_edge("answer", END)
    workflow.add_edge("test", END)

    return workflow.compile(checkpointer=checkpointer or MemorySaver())
