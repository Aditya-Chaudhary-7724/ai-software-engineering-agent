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


@dataclass(frozen=True)
class AnswerCase:
    """F7: a question for the service behind Ask (`HybridRAGService.answer`).

    `answerable=False` cases have NO supporting code in the sample
    repository — their words were chosen to share no full-text-search
    terms with it — so the correct behavior is to acknowledge
    insufficient evidence rather than answer. `expected_terms` are what a
    REAL model's answer should mention (checked only in the real-model
    category); deterministic checks never read the answer text.
    """

    case_id: str
    description: str
    question: str
    answerable: bool
    expected_files: FrozenSet[str] = frozenset()
    expected_terms: Tuple[str, ...] = ()


ANSWER_CASES = [
    AnswerCase(
        case_id="answer-login-route",
        description="Where the login route lives: must cite routes.py with lexical evidence.",
        question="Where is the login route implemented?",
        answerable=True,
        expected_files=frozenset({"routes.py"}),
        expected_terms=("login_route", "routes.py"),
    ),
    AnswerCase(
        case_id="answer-generate-jwt",
        description="What generate_jwt returns: must cite auth_service.py with lexical evidence.",
        question="What does generate_jwt return?",
        answerable=True,
        expected_files=frozenset({"auth_service.py"}),
        expected_terms=("jwt-for-",),
    ),
    AnswerCase(
        case_id="answer-greet-test",
        description="How greet is tested: must cite test_greet.py with lexical evidence.",
        question="Which test checks the greet function?",
        answerable=True,
        expected_files=frozenset({"test_greet.py"}),
        expected_terms=("test_greet",),
    ),
    AnswerCase(
        case_id="answer-insufficient-image-resizing",
        description="Nothing in the repository resizes images: no lexical evidence may be claimed.",
        question="How are uploaded images resized?",
        answerable=False,
    ),
    AnswerCase(
        case_id="answer-insufficient-database-pool",
        description="No database code exists: no lexical evidence may be claimed.",
        question="How is the database connection pool configured?",
        answerable=False,
    ),
]
