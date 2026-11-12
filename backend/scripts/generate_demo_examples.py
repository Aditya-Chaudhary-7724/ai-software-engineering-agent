"""Operator-only: generate the pre-generated public-demo examples for ONE
repository and store them (replacing that repository's previous set).

The input is a JSON file the operator writes for that repository:

    {"questions": ["How is the API token checked?", "..."],   # 1 to 5
     "task": "Add a docstring to the add_numbers function"}    # optional change request

Each question is answered by the real Ask pipeline (with citations); the task
is run by the real agent up to its proposed diff, which is NEVER applied.
These are real LLM calls on the OPERATOR model (LLM_PROVIDER / LLM_MODEL),
so the script refuses to run without --confirm-real-calls, and refuses the
placeholder stub LLM unless --allow-stub (useful only to test the pipeline).
It never writes repository files, agent tasks, persistent checkpoints or
demo quota. Re-run it with --replace after re-indexing a repository, since
citations point at indexed line ranges.

Run from the repository root:

    set -a; source .env; set +a
    .venv311/bin/python backend/scripts/generate_demo_examples.py \\
        --repository-id 3 --input demo-examples/repo3.json --confirm-real-calls

Exit codes: 0 stored, 2 refused before any call, 3 generation failed
(nothing stored; the previous set is kept).
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from api import repository_catalog
from demo.example_generator import ExampleGenerationError, ExampleRequest, generate_examples
from demo.examples import DemoExampleStore
from observability.redaction import redact_text
from rag.llm.base import LLMProvider
from rag.llm.exceptions import LLMConfigurationError
from rag.llm.factory import configured_llm_provider
from sandbox.disabled_runner import configured_test_runner
from vectorstore.embeddings.factory import configured_embedding_provider
from vectorstore.exceptions import EmbeddingConfigurationError, SchemaOutOfDateError
from vectorstore.migrations import require_current_schema
from vectorstore.store import VectorStore


class _NoGraphClient:
    def run(self, query, **params):
        return []

    def close(self):
        pass


def _graph_client():
    try:
        from graph.client import Neo4jClient

        return Neo4jClient()
    except Exception:
        return _NoGraphClient()


def _read_request(path: str) -> ExampleRequest:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise ExampleGenerationError('the input must be {"questions": [...], "task": "..."}')
    questions = [q for q in data["questions"] if isinstance(q, str)]
    task = data.get("task")
    return ExampleRequest(questions=questions, task_instruction=task if isinstance(task, str) else None)


def main(
    argv: Optional[List[str]] = None,
    llm_factory: Callable[[], Tuple[LLMProvider, str]] = configured_llm_provider,
) -> int:
    parser = argparse.ArgumentParser(description="Generate pre-generated demo examples for one repository.")
    parser.add_argument("--repository-id", type=int, required=True)
    parser.add_argument("--input", required=True, help="JSON file with questions and an optional task")
    parser.add_argument("--replace", action="store_true", help="replace this repository's existing examples")
    parser.add_argument("--confirm-real-calls", action="store_true", help="required: makes real LLM calls")
    parser.add_argument("--allow-stub", action="store_true", help="accept the placeholder stub LLM (testing only)")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("Refusing: DATABASE_URL is not set.")
        return 2
    try:
        request = _read_request(args.input)
        request.validate()
    except (OSError, ValueError, ExampleGenerationError) as exc:
        print(f"Refusing: invalid input: {exc}")
        return 2
    conn = VectorStore(database_url).connect(register_vector_type=False)
    try:
        require_current_schema(conn)
    except SchemaOutOfDateError as exc:
        print(f"Refusing: {exc}")
        return 2
    finally:
        conn.close()
    record = repository_catalog.get_repository(database_url, args.repository_id)
    if record is None or not Path(record.root_path).is_dir():
        print(f"Refusing: repository {args.repository_id} does not exist or its files are missing.")
        return 2
    store = DemoExampleStore(database_url)
    if store.count_for_repository(record.id) and not args.replace:
        print(f"Refusing: repository {record.id} already has examples; re-run with --replace to regenerate them.")
        return 2
    try:
        llm_provider, llm_name = llm_factory()
        embedding_provider, embedding_name = configured_embedding_provider()
    except (LLMConfigurationError, EmbeddingConfigurationError) as exc:
        print(f"Refusing: {exc}")
        return 2
    if llm_name == "stub" and not args.allow_stub:
        print("Refusing: the LLM is the placeholder stub (no real provider configured). Use --allow-stub only to test.")
        return 2
    if llm_name != "stub" and not args.confirm_real_calls:
        print(f"Refusing: this makes real {llm_name} calls on the operator model. Re-run with --confirm-real-calls.")
        return 2

    generator = llm_name if llm_name == "stub" else f"{llm_name}:{getattr(llm_provider, 'model', 'default')}"
    graph = _graph_client()
    try:
        examples = generate_examples(
            request=request, repository_id=record.id, root_path=record.root_path, database_url=database_url,
            graph_client=graph, embedding_provider=embedding_provider, llm_provider=llm_provider,
            generator=generator, test_runner=configured_test_runner(),
        )
    except Exception as exc:
        print(f"FAILED, nothing stored (the previous set is kept): {type(exc).__name__}: {redact_text(str(exc))}")
        return 3
    finally:
        graph.close()
    store.replace_for_repository(record.id, examples)
    asks = sum(1 for e in examples if e.kind == "ask")
    tasks = len(examples) - asks
    print(f"Stored {asks} Q&A example(s) and {tasks} task example(s) for repository {record.id} ({record.name}).")
    print(f"generator: {generator}; embeddings: {embedding_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
