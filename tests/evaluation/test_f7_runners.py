"""F7 evaluation runners: answers, modification safety, agent lifecycle and
regression — integration tests against the real test PostgreSQL, plus the
convention every runner here follows: a deliberately WRONG expectation
must be reported as a failure, never as success."""

import os
import tempfile
from pathlib import Path

import psycopg
import pytest

from evaluation.dataset import ANSWER_CASES, AnswerCase
from evaluation.models import KIND_REAL_MODEL
from evaluation.runners import agent_lifecycle, regression
from evaluation.runners.answers import (
    REAL_MODEL_CATEGORY,
    acknowledges_insufficient_evidence,
    run_answer_evaluation,
)
from evaluation.runners.modification_safety import CASES as MODSAFETY_CASES, run_modification_safety_evaluation
from evaluation.safety import UnsafeEvaluationTargetError, require_disposable_root
from rag.llm.base import LLMProvider

from tests.agent_tasks.conftest import clean_agent_tables  # noqa: F401  (fixture)
from tests.evaluation.conftest import requires_postgres, vector_store  # noqa: F401
from tests.vectorstore.conftest import TEST_DATABASE_URL

# -- answers ----------------------------------------------------------------


@requires_postgres
def test_answer_cases_pass_and_real_model_cases_are_skipped_without_a_real_llm(vector_store):  # noqa: F811
    results = run_answer_evaluation(vector_store)

    deterministic = [r for r in results if r.category == "answers"]
    real = [r for r in results if r.category == REAL_MODEL_CATEGORY]
    assert {r.case_id for r in deterministic} == {c.case_id for c in ANSWER_CASES}
    assert all(r.passed for r in deterministic), [(r.case_id, r.failure_reason) for r in deterministic if not r.passed]
    assert len(real) == len(ANSWER_CASES)
    assert all(r.skipped and r.evaluation_kind == KIND_REAL_MODEL for r in real)
    assert all("--real-llm" in r.skip_reason for r in real)


@requires_postgres
def test_answers_runner_reports_wrong_expectations_as_failures(vector_store):  # noqa: F811
    wrong = [
        AnswerCase("wrong-file", "expects a file that is not the answer", "Where is the login route implemented?", True, frozenset({"nope.py"})),
        # "password" IS in the sample repository, so lexical evidence exists.
        AnswerCase("wrongly-unanswerable", "claims no evidence exists", "How is the password checked?", False),
    ]
    results = [r for r in run_answer_evaluation(vector_store, cases=wrong) if r.category == "answers"]
    assert [r.passed for r in results] == [False, False]
    assert "expected_file_cited" in results[0].failure_reason
    assert "no_lexical_evidence_claimed" in results[1].failure_reason


@requires_postgres
def test_insufficient_evidence_is_not_detected_by_the_service_with_hash_embeddings(vector_store):  # noqa: F811
    """Documents a real limitation instead of hiding it: vector search always
    returns its top-k, so the service's own signal (zero context chunks)
    never fires for unanswerable questions."""
    results = run_answer_evaluation(vector_store)
    for result in (r for r in results if r.case_id.startswith("answer-insufficient") and r.category == "answers"):
        flag = next(m for m in result.metrics if m.name.startswith("ask_flags_insufficient_evidence"))
        assert flag.passed is None and flag.value == 0.0
        assert next(m for m in result.metrics if m.name == "context_chunk_count").value > 0


class _ScriptedLLM(LLMProvider):
    def __init__(self, text):
        self.text = text

    def generate(self, prompt, system=None):
        return self.text


@requires_postgres
def test_real_model_category_scores_whatever_provider_it_is_given(vector_store):  # noqa: F811
    """The real-model path, exercised with a scripted provider (no API key
    in this environment): it gates on the answer TEXT, labelled real_model."""
    good = run_answer_evaluation(vector_store, real_llm_provider=_ScriptedLLM("login_route in routes.py; the code does not contain this."))
    bad = run_answer_evaluation(vector_store, real_llm_provider=_ScriptedLLM("Sure! It uses Redis."))
    real_good = [r for r in good if r.category == REAL_MODEL_CATEGORY]
    real_bad = [r for r in bad if r.category == REAL_MODEL_CATEGORY]
    assert all(not r.skipped and r.evaluation_kind == KIND_REAL_MODEL for r in real_good)
    assert [r.passed for r in real_good if "insufficient" in r.case_id] == [True, True]
    assert not any(r.passed for r in real_bad)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("The provided context does not contain any image resizing code.", True),
        ("I cannot determine this from the repository.", True),
        ("There is no information about connection pools.", True),
        ("Images are resized with Pillow in utils.py.", False),
        ("", False),
    ],
)
def test_insufficient_evidence_heuristic(text, expected):
    assert acknowledges_insufficient_evidence(text) is expected


# -- modification safety --------------------------------------------------------


@requires_postgres
def test_modification_safety_cases_all_pass(vector_store):  # noqa: F811
    results = run_modification_safety_evaluation(vector_store)
    assert {r.case_id for r in results} == {c[0] for c in MODSAFETY_CASES}
    assert all(r.passed for r in results), [(r.case_id, r.failure_reason) for r in results if not r.passed]


@requires_postgres
def test_modification_safety_detects_a_missing_protection(vector_store, monkeypatch):  # noqa: F811
    """With the F7 protected-target check disabled, the evaluation must FAIL."""
    import modification.service as service_module

    monkeypatch.setattr(service_module, "_require_writable_target", lambda relative_path: None)
    results = {r.case_id: r for r in run_modification_safety_evaluation(vector_store)}
    assert results["modsafety-protected-targets"].passed is False


@requires_postgres
def test_modification_safety_detects_a_missing_resolved_path_check(vector_store, monkeypatch):  # noqa: F811
    """With the symlink (resolved-target) check disabled, the symlink case
    must FAIL while the name-based protected-target case still passes."""
    import modification.service as service_module

    monkeypatch.setattr(service_module, "_require_writable_resolved_target", lambda root_path, path: None)
    results = {r.case_id: r for r in run_modification_safety_evaluation(vector_store)}
    assert results["modsafety-symlink-targets"].passed is False
    assert "dir_link_into_git_hooks_refused" in (results["modsafety-symlink-targets"].failure_reason or "")
    assert results["modsafety-protected-targets"].passed is True


# -- agent lifecycle -------------------------------------------------------------


def test_lifecycle_is_skipped_without_an_isolated_database():
    results = agent_lifecycle.run_agent_lifecycle_evaluation(None)
    assert len(results) == len(agent_lifecycle.CASES)
    assert all(r.skipped and "isolated database" in r.skip_reason for r in results)


@requires_postgres
def test_lifecycle_cases_pass_against_an_isolated_database(clean_agent_tables):
    results = agent_lifecycle.run_agent_lifecycle_evaluation(TEST_DATABASE_URL, protected_database_url=None)
    assert len(results) == len(agent_lifecycle.CASES)
    assert all(r.passed for r in results), [(r.case_id, r.failure_reason) for r in results if not r.passed]
    conn = psycopg.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_tasks")
        assert cur.fetchone()[0] == 0  # cleaned up after itself
    conn.close()


@requires_postgres
def test_lifecycle_refuses_a_database_with_someone_elses_active_task(clean_agent_tables):
    from agent_tasks.store import AgentTaskStore

    AgentTaskStore(TEST_DATABASE_URL).create(repository_id=424242, target_path="/srv/x", instruction="x", route="answer")
    results = agent_lifecycle.run_agent_lifecycle_evaluation(TEST_DATABASE_URL, protected_database_url=None)
    assert all(r.skipped and "refusing to share" in r.skip_reason for r in results)


@requires_postgres
def test_lifecycle_detects_a_broken_approval_gate(clean_agent_tables, monkeypatch):
    """If the store accepted every decision (no binding check), the stale/
    reused and concurrent cases must FAIL."""
    from agent_tasks.store import AgentTaskStore

    real_get = AgentTaskStore.get

    def accept_everything(self, task_id, *, approved, interrupt_id, diff_sha256):
        return real_get(self, task_id)

    monkeypatch.setattr(AgentTaskStore, "record_decision", accept_everything)
    results = {r.case_id: r for r in agent_lifecycle.run_agent_lifecycle_evaluation(TEST_DATABASE_URL, protected_database_url=None)}
    assert results["lifecycle-stale-reused-mismatched"].passed is False
    assert results["lifecycle-concurrent-decisions"].passed is False

# -- regression ---------------------------------------------------------------


def test_regression_groups_point_at_existing_test_suites():
    for _, _, paths in regression.GROUPS:
        for path in paths:
            assert (regression.REPO_ROOT / path).exists(), path


def test_parse_junit_counts(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuites><testsuite tests="7" failures="1" errors="1" skipped="2"></testsuite>'
        '<testsuite tests="3" failures="0" errors="0" skipped="0"></testsuite></testsuites>'
    )
    assert regression.parse_junit(report) == {"tests": 10, "failures": 1, "errors": 1, "skipped": 2, "passed": 6}


def test_a_failing_regression_group_is_reported_as_failed(tmp_path, monkeypatch):
    failing = tmp_path / "tests" / "test_fails.py"
    failing.parent.mkdir()
    failing.write_text("def test_fails():\n    assert False\n\ndef test_ok():\n    pass\n")
    monkeypatch.setattr(regression, "REPO_ROOT", tmp_path)
    result = regression._run_group("regression-demo", "demo", ("tests/test_fails.py",), TEST_DATABASE_URL)
    assert result.passed is False
    assert next(m for m in result.metrics if m.name == "tests_failed_or_errored").value == 1.0
    assert next(m for m in result.metrics if m.name == "tests_passed").value == 1.0


# -- safety guard -------------------------------------------------------------


def test_require_disposable_root_accepts_only_temporary_directories(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        require_disposable_root(tmp)
    with pytest.raises(UnsafeEvaluationTargetError):
        require_disposable_root(str(Path(__file__).resolve().parents[2]))  # the project itself
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("WORKSPACE_ROOT", tmp)
        checkout = Path(tmp) / "owner" / "repo"
        checkout.mkdir(parents=True)
        with pytest.raises(UnsafeEvaluationTargetError):
            require_disposable_root(str(checkout))


def test_every_f7_runner_that_writes_uses_the_guard():
    import inspect

    from evaluation.runners import modification_safety

    for module in (agent_lifecycle, modification_safety):
        assert "require_disposable_root" in inspect.getsource(module)
    assert os.path.basename(agent_lifecycle.__file__) == "agent_lifecycle.py"
