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
    .venv/bin/python backend/scripts/run_evaluation.py
    .venv/bin/python backend/scripts/run_evaluation.py --json /tmp/eval_report.json
    .venv/bin/python backend/scripts/run_evaluation.py --llm-judge   # requires LLM_API_KEY
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.llm_judge import AnthropicLLMJudge
from evaluation.report import export_json, render_text_report
from evaluation.suite import run_full_suite


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI Software Engineering Agent evaluation suite.")
    parser.add_argument("--json", metavar="PATH", help="Also export the raw results as JSON to this path.")
    parser.add_argument(
        "--llm-judge",
        action="store_true",
        help="Additionally score RAG answers with a real Anthropic model. REQUIRES LLM_API_KEY. Informational only.",
    )
    args = parser.parse_args()

    llm_judge = None
    if args.llm_judge:
        from rag.llm.anthropic_provider import AnthropicLLMProvider

        if not os.environ.get("LLM_API_KEY"):
            print("--llm-judge requires LLM_API_KEY to be set. Skipping the LLM-judge metric.", file=sys.stderr)
        else:
            llm_judge = AnthropicLLMJudge(AnthropicLLMProvider())

    report = run_full_suite(llm_judge=llm_judge)
    print(render_text_report(report))

    if args.json:
        export_json(report, args.json)
        print(f"\nJSON report written to {args.json}")

    sys.exit(0 if report.failed_count == 0 else 1)


if __name__ == "__main__":
    main()
