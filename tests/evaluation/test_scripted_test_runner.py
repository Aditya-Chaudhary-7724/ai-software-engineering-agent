"""Pure unit tests for ScriptedTestRunner — no Docker involved."""

import pytest

from sandbox.exceptions import NoTestCommandError
from sandbox.models import TestRunResult

from evaluation.scripted_test_runner import ScriptedTestRunner, make_result


def test_make_result_passed_defaults():
    result = make_result(passed=True)
    assert result.passed is True
    assert result.exit_code == 0
    assert result.stdout == "1 passed"


def test_make_result_failed_defaults():
    result = make_result(passed=False)
    assert result.passed is False
    assert result.exit_code == 1
    assert result.stdout == "1 failed"


def test_returns_results_in_order():
    runner = ScriptedTestRunner([make_result(passed=False), make_result(passed=True)])

    first = runner.run("/some/path")
    second = runner.run("/some/path")

    assert first.passed is False
    assert second.passed is True
    assert runner.call_count == 2


def test_raises_when_called_more_times_than_scripted():
    runner = ScriptedTestRunner([make_result(passed=True)])
    runner.run("/some/path")

    with pytest.raises(NoTestCommandError):
        runner.run("/some/path")


def test_is_a_real_test_runner_result_type():
    runner = ScriptedTestRunner([make_result(passed=True)])
    result = runner.run("/x")
    assert isinstance(result, TestRunResult)
