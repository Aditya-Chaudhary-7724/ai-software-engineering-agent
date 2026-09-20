"""A deterministic, non-Docker stand-in for `sandbox.base.TestRunner`,
used only by the Phase 12 testing-loop evaluation runner.

NOT a real sandbox and must never be presented as one — same
"deterministic, non-production stand-in" pattern as
`rag/llm/stub_provider.py::StubLLMProvider`. It exists solely so the
agent's fix-loop ROUTING logic (bounded iterations, wall-clock budget,
the approval gate on every retry) can be evaluated deterministically,
without requiring Docker and without depending on whether a stub LLM's
placeholder text happens to pass or fail a real test suite. Real,
Docker-backed sandbox behavior is evaluated separately — see
`docs/architecture.md`'s "Evaluation" section for what this stands in
for versus what still needs Docker.

Lives in `backend/evaluation/`, not `backend/sandbox/`: Phase 10's
`sandbox` package deliberately ships no fake/unsandboxed implementation
of `TestRunner` at all, since "repository code never runs on the host"
is a security invariant of that package, not a swappable backend
choice (see `sandbox/base.py`'s docstring). This scripted runner is
evaluation tooling, not a sandbox alternative — it never executes
anything, sandboxed or otherwise.
"""

from typing import List

from sandbox.base import TestRunner
from sandbox.exceptions import NoTestCommandError
from sandbox.models import TestRunResult


def make_result(passed: bool, stdout: str = "", stderr: str = "") -> TestRunResult:
    return TestRunResult(
        command=["python", "-m", "pytest", "-q"],
        exit_code=0 if passed else 1,
        stdout=stdout or ("1 passed" if passed else "1 failed"),
        stderr=stderr,
        timed_out=False,
        duration_seconds=0.05,
    )


class ScriptedTestRunner(TestRunner):
    """Returns a pre-programmed sequence of `TestRunResult`s, one per
    call, in order. Raises `NoTestCommandError` (the same exception a
    real `DockerTestRunner` raises for a repository with no detectable
    test command) if called more times than results were programmed —
    a bug in a case's expectations should surface as a clear evaluation
    failure, not an opaque `IndexError`.
    """

    def __init__(self, results: List[TestRunResult]) -> None:
        self._results = list(results)
        self.call_count = 0

    def run(self, root_path: str) -> TestRunResult:
        self.call_count += 1
        if not self._results:
            raise NoTestCommandError(
                f"ScriptedTestRunner.run() called a {self.call_count}th time, but only "
                f"{self.call_count - 1} result(s) were programmed for this case."
            )
        return self._results.pop(0)
