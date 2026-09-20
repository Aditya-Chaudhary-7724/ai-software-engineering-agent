"""The Phase 12 benchmark: one small, fixed, human-inspectable sample
repository plus explicit evaluation cases against it.

Deliberately small (4 files, ~15 lines total) rather than a large
corpus — per this phase's own instruction, the dataset must be
"transparent enough that an interviewer can inspect it" in full. Every
case's `expected_*` field is derived directly from reading the sample
repository below, not from running the system and recording whatever
it happened to return.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Tuple


def build_sample_repository(root: Path) -> None:
    """A tiny repository with one real dependency chain (routes.py ->
    auth_service.py, the same worked example Phase 6's hybrid
    retrieval docs use) and one independent, self-testing module
    (greet.py / test_greet.py, used by the modification and
    testing-loop evaluation runners).
    """
    (root / "auth_service.py").write_text(
        "def login_user(username, password):\n"
        "    return generate_jwt(username)\n\n"
        "def generate_jwt(username):\n"
        "    return f'jwt-for-{username}'\n"
    )
    (root / "routes.py").write_text(
        "from auth_service import login_user\n\n"
        "def login_route(request):\n"
        "    return login_user(request.username, request.password)\n"
    )
    (root / "greet.py").write_text("def greet():\n    return 'hi'\n")
    (root / "test_greet.py").write_text(
        "from greet import greet\n\n" "def test_greet():\n" "    assert greet() == 'hi'\n"
    )


@dataclass(frozen=True)
class RetrievalCase:
    """A query plus the set of files a correct retrieval should surface.

    `expected_relevant_files` is decided by reading the sample
    repository's actual content (see `build_sample_repository` above),
    not by running retrieval and recording its output.
    """

    case_id: str
    description: str
    query: str
    expected_relevant_files: FrozenSet[str]
    k: int = 5


@dataclass(frozen=True)
class RAGCase:
    """A query plus the evidence a grounded answer must actually have
    been shown. `expected_context_substrings` are literal strings that
    must appear in the assembled context (built deterministically by
    `rag.context.build_context`, before any LLM is involved) — checking
    these needs no LLM and no judgment call, just a substring test.
    """

    case_id: str
    description: str
    query: str
    expected_evidence_files: FrozenSet[str]
    expected_context_substrings: Tuple[str, ...]


@dataclass(frozen=True)
class AgentCase:
    """A question plus the routing decision it must produce, and log
    substrings that must NOT appear (proving no unnecessary capability
    was invoked for this task type — see docs/architecture.md's
    "Evaluation" section for how "tool selection" is honestly scoped
    given this agent routes deterministically rather than via an
    LLM-driven tool-calling loop).
    """

    case_id: str
    description: str
    question: str
    expected_task_type: str
    expected_decision: str
    expect_awaiting_approval: bool = False
    forbidden_log_substrings: Tuple[str, ...] = ()


RETRIEVAL_CASES = [
    RetrievalCase(
        case_id="retrieval-login-route",
        description="Query about the login route should surface both the route and the function it calls.",
        query="login route",
        expected_relevant_files=frozenset({"auth_service.py", "routes.py"}),
    ),
    RetrievalCase(
        case_id="retrieval-generate-jwt",
        description="Query about JWT generation should surface auth_service.py specifically.",
        query="generate jwt token",
        expected_relevant_files=frozenset({"auth_service.py"}),
    ),
    RetrievalCase(
        case_id="retrieval-greet",
        description="Query about the greet function should surface both its definition and its test.",
        query="greet function test",
        expected_relevant_files=frozenset({"greet.py", "test_greet.py"}),
    ),
]

RAG_CASES = [
    RAGCase(
        case_id="rag-login-route",
        description="Asking where the login route is implemented must show routes.py's actual source.",
        query="Where is the login route implemented?",
        expected_evidence_files=frozenset({"routes.py"}),
        expected_context_substrings=("login_route", "login_user"),
    ),
    RAGCase(
        case_id="rag-generate-jwt",
        description="Asking what generate_jwt returns must show its actual source, not a paraphrase.",
        query="What does generate_jwt return?",
        expected_evidence_files=frozenset({"auth_service.py"}),
        expected_context_substrings=("jwt-for-",),
    ),
]

AGENT_CASES = [
    AgentCase(
        case_id="agent-answer-routing",
        description="An informational question must route to 'answer' and never touch modification or test execution.",
        question="Where is the login route implemented?",
        expected_task_type="answer",
        expected_decision="answer",
        forbidden_log_substrings=("Proposed a change", "Running tests"),
    ),
    AgentCase(
        case_id="agent-test-routing",
        description="A test-running request must route to 'test' and never propose a code change.",
        question="Run the tests",
        expected_task_type="test",
        expected_decision="test",
        forbidden_log_substrings=("Proposed a change",),
    ),
    AgentCase(
        case_id="agent-modify-requires-approval",
        description=(
            "A change request must pause for human approval rather than applying anything "
            "automatically — safe refusal until the missing approval is actually given."
        ),
        question="Fix the login bug",
        expected_task_type="modify",
        expected_decision="modify",
        expect_awaiting_approval=True,
        forbidden_log_substrings=("Applied approved change",),
    ),
]
