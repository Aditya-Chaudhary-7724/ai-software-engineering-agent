"""Phase 12: runs the deterministic evaluation suite and prints a
human-readable report.

Deterministic by default — no LLM_API_KEY needed: retrieval, RAG
evidence/grounding, agent routing, modification safety, and the
testing loop are all evaluated against real PostgreSQL (+ Neo4j and
Docker where a category needs them, skipped honestly if unreachable)
using `StubLLMProvider` and `DeterministicLocalEmbeddingProvider` —
the same explicitly-non-production stand-ins used throughout this
project's own test suite.

Pass `--llm-judge` to additionally score RAG answers with a real
Anthropic model (REQUIRES a real `LLM_API_KEY`) — informational only,
never mixed into the deterministic pass/fail metrics. Never run unless
explicitly requested.

Run from the repository root:

    set -a; source .env; set +a
    .venv311/bin/python backend/scripts/run_evaluation.py
    .venv311/bin/python backend/scripts/run_evaluation.py --json /tmp/eval_report.json
    .venv311/bin/python backend/scripts/run_evaluation.py --llm-judge   # requires LLM_API_KEY

F7 options (all opt-in; see docs/evaluation.md):

    --isolated-database-url URL   write ALL evaluation data to this database instead of
                                  DATABASE_URL, and run the agent-task lifecycle category
                                  there (no API worker may use it, e.g. the pytest DB).
                                  Unreachable or the same database as DATABASE_URL: exit 2.
    --with-regression             also re-run the security/approval pytest suites
                                  and report their exact counts; they run against the
                                  isolated database if given, else TEST_DATABASE_URL.
                                  Unreachable or the same database as DATABASE_URL: exit 2.
    --real-llm                    real-model answer evaluation (LLM_API_KEY)
    --real-embeddings             retrieval/answers with real embeddings (EMBEDDING_API_KEY)
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from evaluation.llm_judge import AnthropicLLMJudge
from evaluation.report import export_json, render_text_report
from evaluation.runners.regression import refusal_reason as regression_refusal_reason
from evaluation.safety import UnsafeEvaluationTargetError, application_database_url, require_distinct_database
from evaluation.suite import run_full_suite


def _remove_leftovers(database_url: str) -> None:
    from graph.client import Neo4jClient
    from vectorstore.store import VectorStore

    from evaluation.cleanup import remove_leftovers
    from evaluation.environment import neo4j_available

    neo4j_client = Neo4jClient() if neo4j_available() else None
    try:
        removed = remove_leftovers(VectorStore(database_url), neo4j_client)
    finally:
        if neo4j_client is not None:
            neo4j_client.close()
    graph = removed["graph_repositories"] if neo4j_client is not None else "Neo4j not reachable, not checked"
    print(f"Removed leftover evaluation data: {removed['repositories']} repositories (PostgreSQL), {graph} (Neo4j).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI Software Engineering Agent evaluation suite.")
    parser.add_argument("--json", metavar="PATH", help="Also export the raw results as JSON to this path.")
    parser.add_argument(
        "--llm-judge",
        action="store_true",
        help="Additionally score RAG answers with a real Anthropic model. REQUIRES LLM_API_KEY. Informational only.",
    )
    parser.add_argument(
        "--isolated-database-url",
        metavar="URL",
        default=os.environ.get("EVALUATION_DATABASE_URL", ""),
        help="Database for the agent-task lifecycle category; must not be served by a running API worker. "
        "Default: $EVALUATION_DATABASE_URL. Without it, that category is reported as skipped.",
    )
    parser.add_argument("--with-regression", action="store_true", help="Re-run the existing security/approval pytest suites.")
    parser.add_argument("--real-llm", action="store_true", help="Real-model answer evaluation. REQUIRES LLM_API_KEY.")
    parser.add_argument("--real-embeddings", action="store_true", help="Use real embeddings. REQUIRES EMBEDDING_API_KEY.")
    parser.add_argument(
        "--remove-leftovers",
        action="store_true",
        help="Only delete evaluation data a KILLED run left behind (eval- temporary namespaces) from the evaluation "
        "database (the isolated one if given, else DATABASE_URL) and Neo4j, then exit. Run when no evaluation is running.",
    )
    args = parser.parse_args()
    # SIGTERM -> SystemExit, so cleanup in `finally` blocks still runs.
    from evaluation.cleanup import exit_on_sigterm

    exit_on_sigterm()
    if args.real_embeddings and not os.environ.get("EMBEDDING_API_KEY"):
        print("--real-embeddings requires EMBEDDING_API_KEY; using the deterministic hash embeddings instead.", file=sys.stderr)

    llm_judge = None
    if args.llm_judge:
        from rag.llm.anthropic_provider import AnthropicLLMProvider

        if not os.environ.get("LLM_API_KEY"):
            print("--llm-judge requires LLM_API_KEY to be set. Skipping the LLM-judge metric.", file=sys.stderr)
        else:
            llm_judge = AnthropicLLMJudge(AnthropicLLMProvider())

    # Fail fast, before any evaluation work: the runners also refuse (and
    # report the cases as skipped), but a refusal here is a usage error.
    # Compared by server identity, not URL text (evaluation/safety.py).
    protected = application_database_url()
    if args.isolated_database_url:
        try:
            require_distinct_database(args.isolated_database_url, protected, purpose="--isolated-database-url")
        except UnsafeEvaluationTargetError as exc:
            print(f"{exc}.", file=sys.stderr)
            sys.exit(2)
        except psycopg.OperationalError:
            # Asked for isolation that cannot be provided: a usage error, never
            # a silent run against DATABASE_URL.
            print("--isolated-database-url (or EVALUATION_DATABASE_URL) is not reachable; refusing to run.", file=sys.stderr)
            sys.exit(2)
    if args.with_regression:
        reason = regression_refusal_reason(protected, args.isolated_database_url or None)
        if reason:
            print(f"--with-regression {reason}.", file=sys.stderr)
            sys.exit(2)

    if args.remove_leftovers:
        _remove_leftovers(args.isolated_database_url or protected)
        return

    report = run_full_suite(
        llm_judge=llm_judge,
        isolated_database_url=args.isolated_database_url or None,
        real_llm=args.real_llm,
        real_embeddings=args.real_embeddings,
        with_regression=args.with_regression,
    )
    print(render_text_report(report))

    if args.json:
        export_json(report, args.json)
        print(f"\nJSON report written to {args.json}")

    sys.exit(0 if report.failed_count == 0 else 1)


if __name__ == "__main__":
    main()
