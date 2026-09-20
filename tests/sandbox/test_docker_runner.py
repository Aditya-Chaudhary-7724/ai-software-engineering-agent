"""Unit tests for DockerTestRunner with `subprocess.run` mocked out —
no real Docker daemon involved (see test_docker_integration.py for
that). Verifies the command construction (security flags actually
present, not just documented), timeout handling, and error mapping.
"""

import subprocess

import pytest

from sandbox.docker_runner import DockerTestRunner
from sandbox.exceptions import DockerUnavailableError, NoTestCommandError
from sandbox.models import PYTHON_SANDBOX_IMAGE, SandboxLimits


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _write_pytest_repo(tmp_path):
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert True\n")


def test_run_raises_no_test_command_error_without_touching_docker(tmp_path, monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called when no test command is detected")

    monkeypatch.setattr(subprocess, "run", _fail_if_called)
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")

    with pytest.raises(NoTestCommandError):
        DockerTestRunner().run(str(tmp_path))


def test_run_raises_docker_unavailable_when_docker_info_fails(tmp_path, monkeypatch):
    _write_pytest_repo(tmp_path)

    def _fake_run(cmd, **kwargs):
        assert cmd[:2] == ["docker", "info"]
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(DockerUnavailableError):
        DockerTestRunner().run(str(tmp_path))


def test_run_builds_command_with_security_restrictions(tmp_path, monkeypatch):
    _write_pytest_repo(tmp_path)
    captured = {}

    def _fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "info"]:
            return _FakeCompletedProcess(returncode=0)
        captured["cmd"] = cmd
        assert kwargs.get("timeout") == 45
        return _FakeCompletedProcess(returncode=0, stdout="1 passed", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    limits = SandboxLimits(timeout_seconds=45, memory_limit="256m", cpu_limit=0.5, pids_limit=64)
    result = DockerTestRunner(limits).run(str(tmp_path))

    cmd = captured["cmd"]
    assert cmd[0:2] == ["docker", "run"]
    assert "--rm" in cmd
    assert "--network" in cmd and cmd[cmd.index("--network") + 1] == "none"
    assert "--memory" in cmd and cmd[cmd.index("--memory") + 1] == "256m"
    assert "--cpus" in cmd and cmd[cmd.index("--cpus") + 1] == "0.5"
    assert "--pids-limit" in cmd and cmd[cmd.index("--pids-limit") + 1] == "64"
    assert "--cap-drop" in cmd and cmd[cmd.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in cmd and cmd[cmd.index("--security-opt") + 1] == "no-new-privileges"
    assert "--read-only" in cmd
    assert "--tmpfs" in cmd
    assert PYTHON_SANDBOX_IMAGE in cmd
    assert cmd[-3:] == ["python", "-m", "pytest"] or cmd[-4:] == ["python", "-m", "pytest", "-q"]
    # No -e/--env-file flag is ever passed: the container never sees host env/secrets.
    assert "-e" not in cmd
    assert "--env-file" not in cmd

    assert result.passed is True
    assert result.exit_code == 0
    assert result.stdout == "1 passed"


def test_run_mounts_a_copy_not_the_original_repository(tmp_path, monkeypatch):
    _write_pytest_repo(tmp_path)
    captured = {}

    def _fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "info"]:
            return _FakeCompletedProcess(returncode=0)
        captured["cmd"] = cmd
        return _FakeCompletedProcess(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    DockerTestRunner().run(str(tmp_path))

    mount_arg = captured["cmd"][captured["cmd"].index("-v") + 1]
    mounted_host_path = mount_arg.split(":")[0]
    assert mounted_host_path != str(tmp_path)
    assert "sandbox-workspace-" in mounted_host_path


def test_network_can_be_enabled_when_explicitly_requested(tmp_path, monkeypatch):
    _write_pytest_repo(tmp_path)
    captured = {}

    def _fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "info"]:
            return _FakeCompletedProcess(returncode=0)
        captured["cmd"] = cmd
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    DockerTestRunner(SandboxLimits(network_disabled=False)).run(str(tmp_path))

    cmd = captured["cmd"]
    assert cmd[cmd.index("--network") + 1] == "bridge"


def test_run_reports_failure_from_nonzero_exit_code(tmp_path, monkeypatch):
    _write_pytest_repo(tmp_path)

    def _fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "info"]:
            return _FakeCompletedProcess(returncode=0)
        return _FakeCompletedProcess(returncode=1, stdout="1 failed", stderr="AssertionError")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    result = DockerTestRunner().run(str(tmp_path))

    assert result.passed is False
    assert result.exit_code == 1
    assert result.timed_out is False


def test_run_kills_container_on_timeout(tmp_path, monkeypatch):
    _write_pytest_repo(tmp_path)
    kill_calls = []

    def _fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "info"]:
            return _FakeCompletedProcess(returncode=0)
        if cmd[:2] == ["docker", "kill"]:
            kill_calls.append(cmd[2])
            return _FakeCompletedProcess(returncode=0)
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr(subprocess, "run", _fake_run)
    result = DockerTestRunner(SandboxLimits(timeout_seconds=1)).run(str(tmp_path))

    assert result.timed_out is True
    assert result.exit_code is None
    assert len(kill_calls) == 1
    assert kill_calls[0].startswith("ai-swe-agent-sandbox-")
