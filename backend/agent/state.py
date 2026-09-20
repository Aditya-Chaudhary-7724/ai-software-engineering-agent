"""Explicit agent state.

Every field is plain data (str/int/bool/list/dict) rather than a
dataclass or provider object, deliberately: LangGraph checkpoints this
state (needed for the human-approval interrupt to survive across
calls), and keeping it plain data avoids any serialization surprises.
Richer objects (SearchResult, Candidate, HybridCandidate) are converted
to dicts at each node boundary via dataclasses.asdict and reconstructed
via types.SimpleNamespace where a downstream function needs attribute
access — see nodes.py.
"""

import operator
from typing import Annotated, Optional, TypedDict

DEFAULT_VECTOR_TOP_K = 10
DEFAULT_KEYWORD_TOP_K = 10
DEFAULT_SEED_LIMIT = 5
DEFAULT_FINAL_TOP_K = 5
MAX_RETRIES = 2

# Phase 10 testing loop: bounded independently by iteration count AND
# wall-clock time, per this project's own "never allow the agent to
# autonomously make unlimited changes" requirement — the loop stops as
# soon as either bound is hit. Either bound alone would leave a gap: a
# fast-failing loop could still spin many times within the time budget,
# and a slow single iteration could run past a reasonable wall-clock
# budget without a hard iteration cap ever being reached.
MAX_FIX_ITERATIONS = 2
MAX_LOOP_SECONDS = 300


class AgentState(TypedDict):
    question: str
    repository_id: int
    root_path: str

    task_type: Optional[str]  # "answer" | "modify" | "test"
    plan: Optional[str]

    vector_top_k: int
    keyword_top_k: int
    seed_limit: int

    vector_hits: list
    keyword_hits: list
    seed_candidates: list
    graph_candidates: list
    ranked_candidates: list

    analysis_summary: Optional[str]
    retry_count: int
    should_retry: bool

    decision: Optional[str]  # "answer" | "modify" | "test"
    approved: Optional[bool]
    # Distinct from `approved`: an approval can still fail to apply (e.g. a
    # stale-change refusal), so routing into the Phase 10 test-verification
    # step reads this, not `approved` — a change was actually written iff
    # this is True.
    applied: Optional[bool]

    # Populated by propose_change (Phase 9) before human_approval, so the
    # interrupt can show the actual diff rather than asking for a blind
    # approval. has_proposal distinguishes "no relevant file was found"
    # (skip approval, report why) from "a change was proposed".
    has_proposal: bool
    proposed_relative_path: Optional[str]
    proposed_original_content: Optional[str]
    proposed_content: Optional[str]
    proposed_diff: Optional[str]

    # Phase 10: drives propose_change_node. None until the first proposal
    # (which uses `question` directly); a fix attempt overwrites it with an
    # instruction built from the failing test output, so the human reviewer
    # sees a diff aimed at the actual failure, not the original request replayed.
    modification_instruction: Optional[str]
    # Set once, on the first post-apply test run, to time.monotonic() +
    # MAX_LOOP_SECONDS — bounds the *whole* propose/approve/apply/test loop,
    # not any single iteration of it.
    loop_deadline: Optional[float]
    fix_iteration: int
    should_retry_fix: bool

    # Populated by the sandbox test runner (Phase 10), whether reached via
    # a standalone "test" request or a post-apply verification run.
    test_ran: bool
    test_passed: Optional[bool]
    test_command: Optional[list]
    test_stdout: Optional[str]
    test_stderr: Optional[str]
    test_exit_code: Optional[int]
    test_timed_out: Optional[bool]

    final_response: Optional[str]
    sources: list

    # Safe execution summaries only (e.g. "Searching repository...") —
    # never hidden chain-of-thought or raw model reasoning. Uses the
    # `operator.add` reducer so each node's log entries append rather
    # than overwrite the running history.
    execution_log: Annotated[list, operator.add]


def initial_state(question: str, repository_id: int, root_path: str) -> AgentState:
    return AgentState(
        question=question,
        repository_id=repository_id,
        root_path=root_path,
        task_type=None,
        plan=None,
        vector_top_k=DEFAULT_VECTOR_TOP_K,
        keyword_top_k=DEFAULT_KEYWORD_TOP_K,
        seed_limit=DEFAULT_SEED_LIMIT,
        vector_hits=[],
        keyword_hits=[],
        seed_candidates=[],
        graph_candidates=[],
        ranked_candidates=[],
        analysis_summary=None,
        retry_count=0,
        should_retry=False,
        decision=None,
        approved=None,
        applied=None,
        has_proposal=False,
        proposed_relative_path=None,
        proposed_original_content=None,
        proposed_content=None,
        proposed_diff=None,
        modification_instruction=None,
        loop_deadline=None,
        fix_iteration=0,
        should_retry_fix=False,
        test_ran=False,
        test_passed=None,
        test_command=None,
        test_stdout=None,
        test_stderr=None,
        test_exit_code=None,
        test_timed_out=None,
        final_response=None,
        sources=[],
        execution_log=[],
    )
