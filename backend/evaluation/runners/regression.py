"""F7 security/approval regression summary — REUSES the existing pytest
suites instead of re-implementing them as evaluation cases.

Each group runs as one `pytest` subprocess writing a JUnit XML report
(stdlib `xml.etree`; no new dependency), and becomes one CaseResult with
the exact passed/failed/error/skipped counts. A group passes only with
zero failures and zero errors; skipped tests are reported, never counted
as passed — e.g. the real-Docker sandbox tests skip without Docker.

Opt-in (`run_evaluation.py --with-regression`) because it re-runs a few
hundred tests; the normal place to run them is `pytest` itself.

Safety: those suites' fixtures DELETE every row of `agent_tasks` (and
other tables) in pytest's database. The runner picks ONE target database —
the isolated evaluation database when one is given (so every evaluation
write goes there), else `TEST_DATABASE_URL` — and refuses to run unless
that target is reachable AND the server reports it is a different database
from the application's DATABASE_URL (`evaluation.safety`). Fail closed: an
unreachable target means isolation cannot be verified, so nothing runs.

The pytest subprocess gets BOTH `TEST_DATABASE_URL` and `DATABASE_URL` set
to that verified target. API routes read `DATABASE_URL` per request, so
without the second override a test that forgot to monkeypatch it would
talk to the developer's real database (exported from `.env`).

A group whose test paths no longer exist FAILS: it is never silently
narrowed, and pytest is never started without paths (which would run the
whole suite under the group's name).
"""

import getpass

import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from evaluation.models import CaseResult, MetricResult
from evaluation.safety import UnsafeEvaluationTargetError, application_database_url, require_distinct_database

CATEGORY = "regression"
REPO_ROOT = Path(__file__).resolve().parents[3]

# (case id, what it protects, test paths) — every path is an existing suite.
GROUPS: List[Tuple[str, str, Tuple[str, ...]]] = [
    (
        "regression-approval",
        "F6 strict, hash-bound, one-time approval (service, graph, store, HTTP, restart)",
        ("tests/agent_tasks", "tests/api/test_v1_agent_tasks.py", "tests/api/test_v1_agent_task_isolation.py"),
    ),
    (
        "regression-security",
        "Phase 14 security suite: approval bypass, path traversal, prompt injection, secrets, sensitive files, sandbox",
        ("tests/security",),
    ),
    (
        "regression-api-boundaries",
        "API auth on every route, read-only Ask, file access isolation, capability honesty",
        ("tests/api/test_v1_auth.py", "tests/api/test_v1_ask.py", "tests/api/test_v1_files.py", "tests/api/test_v1_capabilities.py"),
    ),
    (
        "regression-modification-and-sandbox",
        "modification service (stale, protected, integrity) and Docker sandbox runner",
        ("tests/modification", "tests/sandbox"),
    ),
]


def pytest_database_url() -> str:
    """The database the suites use — must match tests/vectorstore/conftest.py."""
    return os.environ.get("TEST_DATABASE_URL", f"postgresql://{getpass.getuser()}@localhost:5432/ai_swe_agent_test")


def regression_database_url(isolated_database_url: Optional[str] = None) -> str:
    """The one database the re-run suites may write to."""
    return isolated_database_url or pytest_database_url()


def _target_label(isolated_database_url: Optional[str]) -> str:
    return "regression (isolated evaluation database)" if isolated_database_url else "regression (TEST_DATABASE_URL)"


def refusal_reason(protected_database_url: Optional[str], isolated_database_url: Optional[str] = None) -> Optional[str]:
    """Why the suites must not run here, or None when it is safe."""
    if not protected_database_url:
        return None  # no application database: nothing to protect
    target = regression_database_url(isolated_database_url)
    purpose = _target_label(isolated_database_url)
    try:
        psycopg.connect(target, connect_timeout=5).close()
    except psycopg.OperationalError:
        return f"REFUSED: {purpose} is not reachable, so it cannot be verified to be isolated from DATABASE_URL"
    try:
        require_distinct_database(target, protected_database_url, purpose=purpose)
    except UnsafeEvaluationTargetError as exc:
        return f"REFUSED: {exc}"
    return None


def subprocess_environment(database_url: str) -> Dict[str, str]:
    """Parent environment with every database variable pinned to the
    verified target (see module docstring). The last variable tells the
    child's tests/conftest.py guard that TEST_DATABASE_URL == DATABASE_URL
    here is deliberate: `refusal_reason` has already verified, by server
    identity, that this target is not the application's database."""
    return {
        **os.environ,
        "TEST_DATABASE_URL": database_url,
        "DATABASE_URL": database_url,
        "PYTEST_DATABASE_URL_IS_VERIFIED_DISPOSABLE": "1",
    }


def parse_junit(path: Path) -> Dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in counts:
            counts[key] += int(suite.get(key, 0))
    counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    return counts


def _run_group(case_id: str, description: str, paths: Tuple[str, ...], database_url: str) -> CaseResult:
    existing = [p for p in paths if (REPO_ROOT / p).exists()]
    missing = [p for p in paths if p not in existing]
    start = time.monotonic()
    if missing:
        return CaseResult(
            case_id=case_id, category=CATEGORY, description=description, passed=False,
            latency_seconds=0.0,
            failure_reason=f"test path(s) no longer exist: {', '.join(missing)}; update GROUPS instead of narrowing silently",
        )
    with tempfile.TemporaryDirectory(prefix="eval-regression-") as tmp:
        report = Path(tmp) / "junit.xml"
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}", *existing],
            cwd=REPO_ROOT,
            env=subprocess_environment(database_url),
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if not report.exists():
            return CaseResult(
                case_id=case_id, category=CATEGORY, description=description, passed=False,
                latency_seconds=time.monotonic() - start,
                failure_reason=f"pytest produced no report (exit code {completed.returncode})",
            )
        counts = parse_junit(report)
    bad = counts["failures"] + counts["errors"]
    return CaseResult(
        case_id=case_id,
        category=CATEGORY,
        description=description,
        passed=bad == 0 and counts["tests"] > 0,
        metrics=[
            MetricResult(name="tests_passed", value=float(counts["passed"])),
            MetricResult(name="tests_failed_or_errored", value=float(bad), expected=0.0, passed=bad == 0),
            MetricResult(name="tests_skipped (not counted as passed)", value=float(counts["skipped"])),
        ],
        latency_seconds=time.monotonic() - start,
        retrieval_results=list(existing),
        failure_reason=None if bad == 0 else f"{bad} test(s) failed or errored; run: pytest {' '.join(existing)}",
    )


_UNSET = object()


def run_regression_evaluation(protected_database_url: Any = _UNSET, isolated_database_url: Optional[str] = None) -> List[CaseResult]:
    """`protected_database_url` defaults to the application database.
    With `isolated_database_url`, the suites run against it instead of
    TEST_DATABASE_URL."""
    if protected_database_url is _UNSET:
        protected_database_url = application_database_url()
    reason = refusal_reason(protected_database_url, isolated_database_url)
    if reason:
        return skipped_regression(reason)
    target = regression_database_url(isolated_database_url)
    return [_run_group(case_id, description, paths, target) for case_id, description, paths in GROUPS]


def skipped_regression(reason: str) -> List[CaseResult]:
    return [
        CaseResult(case_id=case_id, category=CATEGORY, description=description, passed=False, skipped=True, skip_reason=reason)
        for case_id, description, _ in GROUPS
    ]
