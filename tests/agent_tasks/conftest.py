"""Fixtures for the F6 agent-task / human-approval tests.

Everything runs against the REAL test PostgreSQL (repositories, chunks,
`agent_tasks`, and LangGraph's checkpoint tables) and the REAL agent graph,
modification service and PostgresSaver. Replaced, deliberately:
- the LLM, by `StubLLMProvider` (deterministic; its "proposed content" is
  placeholder text — enough to prove propose -> diff -> approve -> apply);
- Neo4j, by `NoGraphClient` (no rows), so these tests don't need Neo4j —
  graph evidence is optional for the agent exactly as it is for Ask;
- the Docker sandbox, by `FakeTestRunner` (tests/agent/conftest.py).
"""

import uuid

import psycopg
import pytest

from agent.checkpointing import connect_checkpoint_database, postgres_checkpointer, setup_postgres_checkpointer
from agent.service import AgentService
from agent_tasks.executor import AgentTaskDependencies, AgentTaskExecutor
from agent_tasks.store import AgentTaskStore
from observability.recorder import NullRecorder
from observability.tracer import Tracer
from rag.llm.stub_provider import StubLLMProvider
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService

from tests.agent.conftest import FakeTestRunner, make_test_result
from tests.vectorstore.conftest import TEST_DATABASE_URL, requires_postgres, vector_store  # noqa: F401

__all__ = [
    "TEST_DATABASE_URL",
    "requires_postgres",
    "vector_store",
    "FakeTestRunner",
    "make_test_result",
    "NoGraphClient",
]

ORIGINAL_CONTENT = "def greet():\n    return 'hi'\n"
MODIFY_INSTRUCTION = "Fix the greet function"


class NoGraphClient:
    def run(self, query, **params):
        return []

    def close(self):
        pass


@pytest.fixture
def clean_agent_tables(vector_store):  # noqa: F811
    setup_postgres_checkpointer(TEST_DATABASE_URL)
    _clear()
    yield
    _clear()


def _clear():
    conn = psycopg.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM agent_tasks")
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                cur.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def indexed_repo(tmp_path, vector_store, clean_agent_tables):  # noqa: F811
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text(ORIGINAL_CONTENT)
    result = IndexingService(DeterministicLocalEmbeddingProvider(), vector_store).index_repository(str(root))
    return {"root": root, "root_path": str(root.resolve()), "repository_id": result.repository_id}


def make_service(vector_store, conn, test_runner=None):  # noqa: F811
    return AgentService(
        vector_store,
        NoGraphClient(),
        DeterministicLocalEmbeddingProvider(),
        StubLLMProvider(),
        test_runner or FakeTestRunner([make_test_result(passed=True)] * 5),
        tracer=Tracer(NullRecorder()),
        checkpointer=postgres_checkpointer(conn),
    )


@pytest.fixture
def checkpoint_conn():
    conn = connect_checkpoint_database(TEST_DATABASE_URL)
    yield conn
    conn.close()


def make_executor(test_runner=None, **overrides):
    runner = test_runner or FakeTestRunner([make_test_result(passed=True)] * 5)
    deps = AgentTaskDependencies(
        database_url=TEST_DATABASE_URL,
        embedding_provider_factory=lambda: (DeterministicLocalEmbeddingProvider(), "deterministic_local"),
        llm_provider_factory=lambda: (StubLLMProvider(), "stub"),
        neo4j_client_factory=NoGraphClient,
        test_runner_factory=lambda: runner,
        tracer_factory=lambda: Tracer(NullRecorder()),
        **overrides,
    )
    store = AgentTaskStore(TEST_DATABASE_URL)
    return store, AgentTaskExecutor(store, deps)


def checkpoint_exists(thread_id: str) -> bool:
    conn = psycopg.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM checkpoints WHERE thread_id = %s LIMIT 1", (thread_id,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def new_thread_id() -> str:
    return str(uuid.uuid4())
