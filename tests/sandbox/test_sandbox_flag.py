"""SANDBOX_ENABLED: an explicit, fail-closed switch for agent-task test
execution (backend/config.py). Default OFF.

Before this flag, a deployment had no sandbox only because the API image
happened to lack a Docker CLI; a Docker CLI and socket appearing would
silently have turned untrusted test execution on.
"""

import subprocess
import sys
from pathlib import Path

import pytest

import config
import sandbox.disabled_runner as disabled_runner_module
from agent_tasks.executor import AgentTaskDependencies
from api.v1 import API_V1_PREFIX
from sandbox.disabled_runner import DisabledTestRunner, configured_test_runner
from sandbox.docker_runner import DockerTestRunner
from sandbox.exceptions import SandboxDisabledError

from tests.api.conftest import api_client, app, auth_headers  # noqa: F401

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"


@pytest.mark.parametrize("value", [None, "", "false", "False", "0", "1", "yes", "on", "enabled", "truee"])
def test_the_sandbox_is_disabled_unless_the_value_is_exactly_true(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("SANDBOX_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SANDBOX_ENABLED", value)
    assert config.sandbox_enabled() is False
    assert isinstance(configured_test_runner(), DisabledTestRunner)


@pytest.mark.parametrize("value", ["true", "TRUE", " True "])
def test_true_enables_the_docker_runner(monkeypatch, value):
    monkeypatch.setenv("SANDBOX_ENABLED", value)
    assert config.sandbox_enabled() is True
    assert isinstance(configured_test_runner(), DockerTestRunner)


def test_the_disabled_runner_executes_nothing(monkeypatch, tmp_path):
    (tmp_path / "test_x.py").write_text("def test_x():\n    assert True\n")

    def no_subprocess(*args, **kwargs):
        raise AssertionError("a disabled sandbox must never start a process")

    monkeypatch.setattr(subprocess, "run", no_subprocess)
    monkeypatch.setattr(subprocess, "Popen", no_subprocess)
    with pytest.raises(SandboxDisabledError, match="sandbox disabled in this deployment"):
        DisabledTestRunner().run(str(tmp_path))


def test_the_agent_worker_uses_the_flag_aware_runner_by_default():
    deps = AgentTaskDependencies(database_url="postgresql://unused")
    assert deps.test_runner_factory is configured_test_runner


def test_the_flag_is_read_per_task_not_at_import(monkeypatch):
    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    assert isinstance(disabled_runner_module.configured_test_runner(), DockerTestRunner)
    monkeypatch.setenv("SANDBOX_ENABLED", "false")
    assert isinstance(disabled_runner_module.configured_test_runner(), DisabledTestRunner)


def _check_script(extra_env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BACKEND_DIR / "scripts" / "check_production_config.py")],
        cwd=BACKEND_DIR,
        env={"PATH": "/usr/bin:/bin", "DATABASE_URL": "postgresql://user@localhost/db", **extra_env},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_check_production_config_reports_the_sandbox_disabled_by_default():
    result = _check_script({})
    assert result.returncode == 0
    assert "Feature flags (effective state):" in result.stdout
    assert any(line.split() == ["SANDBOX_ENABLED", "disabled"] for line in result.stdout.splitlines())


def test_check_production_config_reports_the_sandbox_enabled():
    result = _check_script({"SANDBOX_ENABLED": "true"})
    assert result.returncode == 0
    assert any(line.split() == ["SANDBOX_ENABLED", "enabled"] for line in result.stdout.splitlines())


def test_capabilities_report_a_disabled_sandbox_without_probing_docker(api_client, monkeypatch):  # noqa: F811
    import api.routes.capabilities as capabilities_module

    monkeypatch.delenv("SANDBOX_ENABLED", raising=False)

    def no_probe(*args, **kwargs):
        raise AssertionError("a disabled sandbox must not probe Docker")

    monkeypatch.setattr(capabilities_module, "docker_available", no_probe)
    monkeypatch.setattr(capabilities_module.subprocess, "run", no_probe)

    body = api_client.get(f"{API_V1_PREFIX}/capabilities", headers=auth_headers()).json()
    assert body["sandbox_enabled"] is False
    assert body["sandbox_docker_available"] is False
    assert body["sandbox_image_available"] is False
    assert body["features"]["test_execution"] is False


def test_capabilities_probe_docker_when_enabled(api_client, monkeypatch):  # noqa: F811
    import api.routes.capabilities as capabilities_module

    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setattr(capabilities_module, "docker_available", lambda: True)
    monkeypatch.setattr(capabilities_module, "_sandbox_image_available", lambda: True)

    body = api_client.get(f"{API_V1_PREFIX}/capabilities", headers=auth_headers()).json()
    assert body["sandbox_enabled"] is True
    assert body["sandbox_docker_available"] is True
    assert body["sandbox_image_available"] is True
