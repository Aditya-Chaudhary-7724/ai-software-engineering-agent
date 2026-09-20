"""Tracing wrappers for existing provider interfaces (`LLMProvider`,
`TestRunner`).

Each wrapper implements the EXACT SAME interface it wraps (pure
decorator pattern) so it can be substituted in anywhere the real
provider is expected, with zero change to calling code —
`ModificationService`, `RAGService`, `HybridRAGService`, and every
`agent/nodes.py` node keep calling `.generate()`/`.run()` exactly as
before, with no idea observability exists. This is what "integrate
tracing into existing execution" means for code that predates this
phase and must not be rewritten.
"""

from typing import Optional

from sandbox.base import TestRunner
from sandbox.models import TestRunResult

from rag.llm.base import LLMProvider

from observability.tracer import Tracer


class TracedLLMProvider(LLMProvider):
    def __init__(self, provider: LLMProvider, tracer: Tracer) -> None:
        self._provider = provider
        self._tracer = tracer

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        attributes = {
            "llm_provider": type(self._provider).__name__,
            "prompt_length": len(prompt),
            "has_system_prompt": system is not None,
            # Token usage is deliberately NOT recorded here: the current
            # LLMProvider interface (rag/llm/base.py) returns a plain
            # string from `generate()`, not a usage object — there is
            # nothing real to report. See docs/architecture.md's
            # "Observability" section for what extending the interface
            # to expose usage would require.
        }
        with self._tracer.span(None, "llm_call", "llm", attributes=attributes) as span:
            response = self._provider.generate(prompt, system=system)
            span.attributes["response_length"] = len(response)
            return response


class TracedTestRunner(TestRunner):
    def __init__(self, runner: TestRunner, tracer: Tracer) -> None:
        self._runner = runner
        self._tracer = tracer

    def run(self, root_path: str) -> TestRunResult:
        with self._tracer.span(None, "sandbox_run", "sandbox", attributes={"runner": type(self._runner).__name__}) as span:
            result = self._runner.run(root_path)
            span.attributes.update(
                {
                    "passed": result.passed,
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                    "duration_seconds": result.duration_seconds,
                }
            )
            return result
