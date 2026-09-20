"""Manual, human-readable demonstration of Phase 7's stateful agent,
updated for Phase 9's real modification wiring and Phase 10's real
testing loop.

Runs against one small repository:
1. An informational question -> real grounded answer (Phase 6 hybrid retrieval).
2. A test-running request -> real sandbox execution (Phase 10); this sample
   repository has no test files, so the honest result is "no supported
   test command was detected", not a fake pass.
3. A code-change request -> pauses for human approval, showing the actual
   diff (a real LangGraph interrupt/resume, not simulated) -> if approved,
   Phase 9's ModificationService really writes the change to disk, then
   Phase 10 runs test verification in the sandbox (again: no test files
   here, so it honestly reports that rather than a fake outcome).
4. A SEPARATE repository that DOES have a test file -> the full Phase 10
   loop end-to-end with a real Docker container: apply -> run tests in
   the sandbox -> tests fail (StubLLMProvider's placeholder text is not
   valid Python) -> a human-approved fix attempt -> fails again -> the
   agent honestly gives up after MAX_FIX_ITERATIONS, never applying an
   unbounded number of unsupervised changes.

Uses DeterministicLocalEmbeddingProvider and StubLLMProvider (both
explicitly non-production stand-ins) — the written content is the stub's
fixed placeholder text, not valid code; this demonstrates the mechanism,
not code-generation quality (that needs a real LLM_API_KEY). Requires
reachable PostgreSQL (with pgvector), Neo4j, and Docker Desktop with the
`ai-swe-agent-sandbox-python:latest` image built (see README.md's Phase
10 section) for real sandboxed test execution. Cleans up everything it
creates.

Note: LangGraph's checkpoint module emits a harmless
LangChainPendingDeprecationWarning on import in this version — it
comes from the library itself, not this project, and does not affect
correctness.

Run from the repository root:

    set -a; source .env; set +a
    .venv/bin/python backend/scripts/manual_agent_demo.py
"""

import getpass
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore

from graph.builder import GraphBuilder
from graph.client import Neo4jClient
from graph.schema import apply_constraints

from rag.llm.stub_provider import StubLLMProvider

from sandbox.docker_runner import DockerTestRunner

from observability.recorder import JSONFileRecorder
from observability.tracer import Tracer

from agent.service import AgentService

DATABASE_URL = os.environ.get(
    "DATABASE_URL", f"postgresql://{getpass.getuser()}@localhost:5432/ai_swe_agent"
)


def build_sample_repository(root: Path) -> None:
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


def build_repository_with_test(root: Path) -> None:
    """A single file containing both the function under test and its own
    pytest test — deliberately one file, so ModificationService's
    file-finder (which always targets exactly one file) has nothing to
    disambiguate, and `sandbox.command_detection` recognizes it as a test
    file by its `test_`-prefixed name.
    """
    (root / "test_greet.py").write_text(
        "def greet():\n    return 'hi'\n\n\ndef test_greet():\n    assert greet() == 'hi'\n"
    )


def main() -> None:
    cleanup_root_paths = []

    with tempfile.TemporaryDirectory(prefix="agent-demo-") as tmp_dir, tempfile.TemporaryDirectory(
        prefix="agent-demo-tests-"
    ) as tmp_dir_with_tests:
        root = Path(tmp_dir)
        build_sample_repository(root)
        root_with_tests = Path(tmp_dir_with_tests)
        build_repository_with_test(root_with_tests)

        ingestion_result = IngestionService().ingest(str(root))
        parsing_result = ParsingService().parse_repository(str(root), ingestion_result)
        ingestion_result_2 = IngestionService().ingest(str(root_with_tests))
        parsing_result_2 = ParsingService().parse_repository(str(root_with_tests), ingestion_result_2)

        embedding_provider = DeterministicLocalEmbeddingProvider()
        vector_store = VectorStore(DATABASE_URL)
        index_result = IndexingService(embedding_provider, vector_store).index_repository(str(root))
        index_result_2 = IndexingService(embedding_provider, vector_store).index_repository(str(root_with_tests))

        neo4j_client = Neo4jClient()
        try:
            apply_constraints(neo4j_client)
            GraphBuilder(neo4j_client).build(str(root), ingestion_result, parsing_result)
            GraphBuilder(neo4j_client).build(str(root_with_tests), ingestion_result_2, parsing_result_2)

            test_runner = DockerTestRunner()
            # Phase 13: a real JSONFileRecorder-backed tracer, so every
            # question/approval/fix-loop below produces a real, locally
            # inspectable trace — run this script, then:
            #   .venv/bin/python backend/scripts/inspect_trace.py --list
            #   .venv/bin/python backend/scripts/inspect_trace.py <thread_id>
            tracer = Tracer(JSONFileRecorder())
            service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), test_runner, tracer)
            root_path = str(root.resolve())
            root_path_2 = str(root_with_tests.resolve())
            cleanup_root_paths.extend([root_path, root_path_2])

            def run_and_print(label, question, repository_id, this_root_path, thread_id):
                print(f"--- {label} ---")
                print(f"Question: {question}")
                result = service.run(question, repository_id, this_root_path, thread_id)
                for entry in result.execution_log:
                    print(f"  [log] {entry}")
                if result.status == "awaiting_approval":
                    print(f"  -> PAUSED: {result.interrupt_message['message']}")
                    diff = result.interrupt_message.get("diff")
                    if diff:
                        print(f"     Diff shown to the human reviewer (before any approval decision):\n{diff}")
                else:
                    print(f"  -> Response: {result.final_response}")
                print()
                return result

            run_and_print(
                "1. Answer", "Where is the login route implemented?", index_result.repository_id, root_path,
                str(uuid.uuid4()),
            )
            run_and_print("2. Test (Phase 10 sandbox)", "Run the tests", index_result.repository_id, root_path, str(uuid.uuid4()))

            modify_thread = str(uuid.uuid4())
            run_and_print(
                "3. Modify (awaiting approval)", "Fix the login bug", index_result.repository_id, root_path,
                modify_thread,
            )

            print("--- 3b. Resuming with human approval = True (no test files in this repo) ---")
            resumed = service.resume(thread_id=modify_thread, approved=True)
            for entry in resumed.execution_log[-2:]:
                print(f"  [log] {entry}")
            print(f"  -> Response: {resumed.final_response}")

            print(
                "--- 4. Modify + Phase 10 testing loop, real Docker sandbox "
                "(requires the ai-swe-agent-sandbox-python:latest image) ---"
            )
            loop_thread = str(uuid.uuid4())
            loop_result = run_and_print(
                "4. Modify (awaiting approval)", "Fix the greet function", index_result_2.repository_id,
                root_path_2, loop_thread,
            )
            round_num = 0
            seen_log_entries = len(loop_result.execution_log)
            while loop_result.status == "awaiting_approval":
                round_num += 1
                print(f"--- 4.{round_num} Resuming with human approval = True ---")
                loop_result = service.resume(thread_id=loop_thread, approved=True)
                for entry in loop_result.execution_log[seen_log_entries:]:
                    print(f"  [log] {entry}")
                seen_log_entries = len(loop_result.execution_log)
                if loop_result.status == "awaiting_approval":
                    assert loop_result.interrupt_message is not None
                    print(f"  -> PAUSED again: {loop_result.interrupt_message['message']}")
                else:
                    print(f"  -> Response: {loop_result.final_response}")
                print()

            print("--- Traces recorded (Phase 13) ---")
            print("  Every question above got its own trace, keyed by its thread_id. Inspect one with:")
            print("    .venv/bin/python backend/scripts/inspect_trace.py <thread_id>")
            print("  or list every trace this run produced:")
            print("    .venv/bin/python backend/scripts/inspect_trace.py --list")
            for label, tid in [("3. Modify", modify_thread), ("4. Modify + testing loop", loop_thread)]:
                print(f"  [{label}] thread_id / trace_id = {tid}")
        finally:
            conn = vector_store.connect()
            with conn.cursor() as cur:
                cur.execute("TRUNCATE code_chunks, repositories RESTART IDENTITY CASCADE;")
            conn.commit()
            conn.close()

            for cleanup_root_path in cleanup_root_paths:
                neo4j_client.run(
                    "MATCH (n {repository_root_path: $root_path}) DETACH DELETE n", root_path=cleanup_root_path
                )
                neo4j_client.run(
                    "MATCH (r:Repository {root_path: $root_path}) DETACH DELETE r", root_path=cleanup_root_path
                )
            neo4j_client.close()


if __name__ == "__main__":
    main()
