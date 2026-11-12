"""Manual Groq smoke test: exactly ONE small Ask-style call, then exit.

Proves that the configured key, model id and endpoint work, using the real
factory (rag/llm/factory.py) and the real Ask prompt (rag/prompt.py) over a
tiny inline code snippet. No database, no indexing, no retries.

It makes a REAL network call that spends a little of your Groq free-tier
quota, so it refuses to run without --confirm-real-call. Prints the
provider, model, latency and the token usage Groq reported. The API key is
never printed; error text is redacted before printing.

Run from the repository root, with GROQ_API_KEY and LLM_MODEL in .env:

    set -a; source .env; set +a
    LLM_PROVIDER=groq .venv/bin/python backend/scripts/manual_groq_smoke.py --confirm-real-call

Exit codes: 0 success, 2 refused before calling (flag missing, not Groq,
invalid configuration), 3 quota/rate limit, 4 any other call failure.
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from observability.redaction import REDACTED_MARKER, redact_text
from rag.llm.exceptions import LLMConfigurationError, LLMQuotaExceededError
from rag.llm.factory import configured_llm_provider
from rag.prompt import SYSTEM_PROMPT, build_prompt

QUESTION = "What does add_numbers return?"
CONTEXT = "File: src/calculator.py (lines 1-2)\ndef add_numbers(a, b):\n    return a + b\n"
_ANSWER_PREVIEW_CHARS = 300


def _scrub(text: str) -> str:
    """Pattern-based redaction, plus removal of the exact configured key:
    pattern matching depends on the key's shape, an exact match does not."""
    key = os.environ.get("GROQ_API_KEY", "")
    if key:
        text = text.replace(key, REDACTED_MARKER)
    return redact_text(text)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="One real Groq call through the LLM factory.")
    parser.add_argument("--confirm-real-call", action="store_true", help="Required: this spends Groq quota.")
    args = parser.parse_args(argv)
    if not args.confirm_real_call:
        print("Refusing: this makes one REAL Groq call. Re-run with --confirm-real-call.")
        return 2

    name = config.llm_provider_name()
    if name != "groq":
        print(f"Refusing: the configured LLM provider is '{name}', not 'groq' (set LLM_PROVIDER=groq). No call made.")
        return 2
    try:
        provider, _ = configured_llm_provider()
    except LLMConfigurationError as exc:
        print(f"Refusing: invalid LLM configuration: {_scrub(str(exc))}. No call made.")
        return 2

    model = getattr(provider, "model", "?")
    print(f"provider: {name}")
    print(f"model: {model}")
    print(f"max_completion_tokens: {config.llm_max_completion_tokens()}")
    start = time.monotonic()
    try:
        answer = provider.generate(build_prompt(QUESTION, CONTEXT), system=SYSTEM_PROMPT)
    except LLMQuotaExceededError as exc:
        print(f"latency: {time.monotonic() - start:.2f}s")
        print(f"RATE LIMITED / QUOTA REACHED (retry after: {exc.retry_after_seconds or 'not given'} s)")
        return 3
    except Exception as exc:  # report, never crash with a traceback that could echo request details
        print(f"latency: {time.monotonic() - start:.2f}s")
        print(f"CALL FAILED: {type(exc).__name__}: {_scrub(str(exc))}")
        return 4

    latency = time.monotonic() - start
    usage = getattr(provider, "last_usage", None)
    print(f"latency: {latency:.2f}s")
    if usage is None:
        print("token usage: not reported by the provider")
    else:
        reasoning = "not reported" if usage.reasoning_tokens is None else usage.reasoning_tokens
        print(
            f"token usage: prompt={usage.prompt_tokens} completion={usage.completion_tokens} "
            f"(reasoning={reasoning}) total={usage.total_tokens}"
        )
    preview = answer if len(answer) <= _ANSWER_PREVIEW_CHARS else answer[:_ANSWER_PREVIEW_CHARS] + "..."
    print(f"answer: {_scrub(preview)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
