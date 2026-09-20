"""Real Docker smoke/integration test — no mocks. Actually builds a
temporary repository, actually invokes the `docker` CLI, and actually
runs pytest inside a container using the
`ai-swe-agent-sandbox-python:latest` image (see
backend/sandbox/docker/python-test.Dockerfile). Skips cleanly if Docker
isn't reachable or the image hasn't been built locally — see
tests/sandbox/conftest.py — rather than failing, per this project's
"LOCAL TESTED vs REQUIRES EXTERNAL SERVICE" distinction.
"""

import subprocess

from sandbox.docker_runner import DockerTestRunner
from sandbox.models import SandboxLimits

from tests.sandbox.conftest import requires_sandbox_image


@requires_sandbox_image
def test_real_container_runs_a_passing_test_suite(tmp_path):
    (tmp_path / "test_math.py").write_text("def test_addition():\n    assert 1 + 1 == 2\n")

    result = DockerTestRunner(SandboxLimits(timeout_seconds=30)).run(str(tmp_path))

    assert result.passed is True
    assert result.exit_code == 0
    assert "1 passed" in result.stdout
    assert result.timed_out is False


@requires_sandbox_image
def test_real_container_reports_a_failing_test_suite(tmp_path):
    (tmp_path / "test_math.py").write_text("def test_addition():\n    assert 1 + 1 == 3\n")

    result = DockerTestRunner(SandboxLimits(timeout_seconds=30)).run(str(tmp_path))

    assert result.passed is False
    assert result.exit_code == 1
    assert "1 failed" in result.stdout


@requires_sandbox_image
def test_real_container_has_no_network_access(tmp_path):
    """Verifies the --network none restriction actually holds against a
    real container, not just that the flag was passed (see
    test_docker_runner.py for the mocked command-construction check).
    """
    (tmp_path / "test_network.py").write_text(
        "import socket\n"
        "def test_network_is_blocked():\n"
        "    try:\n"
        "        socket.create_connection(('8.8.8.8', 53), timeout=3)\n"
        "        assert False, 'network should be blocked in the sandbox'\n"
        "    except OSError:\n"
        "        pass\n"
    )

    result = DockerTestRunner(SandboxLimits(timeout_seconds=15)).run(str(tmp_path))

    assert result.passed is True, result.stdout + result.stderr


@requires_sandbox_image
def test_real_container_root_filesystem_is_read_only(tmp_path):
    (tmp_path / "test_readonly.py").write_text(
        "def test_cannot_write_outside_workspace():\n"
        "    try:\n"
        "        open('/etc/pwned', 'w').write('x')\n"
        "        assert False, 'root filesystem should be read-only'\n"
        "    except OSError:\n"
        "        pass\n"
    )

    result = DockerTestRunner(SandboxLimits(timeout_seconds=15)).run(str(tmp_path))

    assert result.passed is True, result.stdout + result.stderr


@requires_sandbox_image
def test_real_container_does_not_mutate_the_original_repository(tmp_path):
    (tmp_path / "test_write.py").write_text(
        "from pathlib import Path\n"
        "def test_writes_a_file():\n"
        "    Path('created_by_test.txt').write_text('hello')\n"
        "    assert Path('created_by_test.txt').exists()\n"
    )

    result = DockerTestRunner(SandboxLimits(timeout_seconds=15)).run(str(tmp_path))

    assert result.passed is True, result.stdout + result.stderr
    # The container wrote into its own copy of the workspace, not the real
    # repository — the original tmp_path must be untouched.
    assert not (tmp_path / "created_by_test.txt").exists()


@requires_sandbox_image
def test_real_container_is_killed_and_removed_on_timeout(tmp_path):
    (tmp_path / "test_slow.py").write_text("import time\ndef test_sleeps_forever():\n    time.sleep(60)\n")

    result = DockerTestRunner(SandboxLimits(timeout_seconds=2)).run(str(tmp_path))

    assert result.timed_out is True
    assert result.exit_code is None

    leftover = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=ai-swe-agent-sandbox-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert leftover.stdout.strip() == ""
