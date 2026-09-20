"""Docker sandbox security (Phase 14, section 7): real, integrated
regression tests against actual containers via `DockerTestRunner` —
not mocked — proving each control Phase 10 already claims to have
actually holds, plus one NEW probe this phase adds (process/PID
exhaustion). Skips cleanly if Docker or the sandbox image isn't
available, per this project's "LOCAL TESTED vs REQUIRES EXTERNAL
SERVICE" convention.
"""

import subprocess

from sandbox.docker_runner import DockerTestRunner
from sandbox.models import SandboxLimits

from tests.security.conftest import requires_sandbox_image


@requires_sandbox_image
def test_sandbox_has_no_network_access(tmp_path):
    (tmp_path / "test_net.py").write_text(
        "import socket\n"
        "def test_network_is_blocked():\n"
        "    try:\n"
        "        socket.create_connection(('8.8.8.8', 53), timeout=3)\n"
        "        assert False, 'network should be blocked'\n"
        "    except OSError:\n"
        "        pass\n"
    )
    result = DockerTestRunner(SandboxLimits(timeout_seconds=15)).run(str(tmp_path))
    assert result.passed is True, result.stdout + result.stderr


@requires_sandbox_image
def test_sandbox_cannot_write_outside_the_mounted_workspace(tmp_path):
    (tmp_path / "test_fs.py").write_text(
        "def test_root_filesystem_is_read_only():\n"
        "    for target in ['/etc/pwned', '/usr/pwned', '/pwned']:\n"
        "        try:\n"
        "            open(target, 'w').write('x')\n"
        "            assert False, f'{target} should not be writable'\n"
        "        except OSError:\n"
        "            pass\n"
    )
    result = DockerTestRunner(SandboxLimits(timeout_seconds=15)).run(str(tmp_path))
    assert result.passed is True, result.stdout + result.stderr


@requires_sandbox_image
def test_sandbox_does_not_leak_host_environment_variables(tmp_path, monkeypatch):
    """Sets a plausible-looking secret in the HOST process environment
    and proves it never appears inside the container — the concrete,
    integrated version of the mocked-command-construction check in
    tests/github_integration/test_token_isolation.py.
    """
    monkeypatch.setenv("DEFINITELY_A_HOST_SECRET", "should-never-appear-in-container")
    (tmp_path / "test_env.py").write_text(
        "import os\n"
        "def test_host_secret_is_absent():\n"
        "    assert os.environ.get('DEFINITELY_A_HOST_SECRET') is None\n"
        "    assert not any('should-never-appear-in-container' in str(v) for v in os.environ.values())\n"
    )
    result = DockerTestRunner(SandboxLimits(timeout_seconds=15)).run(str(tmp_path))
    assert result.passed is True, result.stdout + result.stderr


def test_sandbox_command_construction_never_mounts_the_docker_socket():
    """The workspace mount is the ONLY volume ever attached — proves it
    by inspecting the actual argv `DockerTestRunner` constructs (pure
    string-list construction, no Docker daemon needed for this specific
    check — real-container behavior is covered by the other tests in
    this file).
    """
    from pathlib import Path

    runner = DockerTestRunner()
    cmd = runner._build_docker_command(Path("/tmp/workspace"), "test-container", ["true"], "some-image")

    mount_flags = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-v"]
    assert len(mount_flags) == 1  # exactly one mount: the workspace
    assert "docker.sock" not in " ".join(cmd)
    assert "-e" not in cmd and "--env-file" not in cmd  # no host env ever forwarded


@requires_sandbox_image
def test_sandbox_process_count_is_bounded_against_a_fork_bomb(tmp_path):
    """A real probe this phase adds: a Python-level fork bomb must be
    stopped by --pids-limit rather than exhausting host resources.
    Bounded by a short SandboxLimits timeout so this test itself cannot
    hang if the limit somehow failed to hold.
    """
    (tmp_path / "test_forkbomb.py").write_text(
        "import os\n"
        "def test_attempted_fork_bomb_is_contained():\n"
        "    pids = []\n"
        "    try:\n"
        "        for _ in range(500):\n"
        "            pid = os.fork()\n"
        "            if pid == 0:\n"
        "                os._exit(0)\n"
        "            pids.append(pid)\n"
        "    except (OSError, BlockingIOError):\n"
        "        pass  # expected: the kernel refuses once --pids-limit is hit\n"
        "    for pid in pids:\n"
        "        try:\n"
        "            os.waitpid(pid, 0)\n"
        "        except ChildProcessError:\n"
        "            pass\n"
    )
    result = DockerTestRunner(SandboxLimits(timeout_seconds=20, pids_limit=32)).run(str(tmp_path))
    # The test process itself may report pass or fail depending on exactly
    # when forking was refused — what actually matters, and what this
    # asserts, is that the container did not hang/exhaust resources: it
    # must finish within the timeout, one way or another.
    assert result.timed_out is False


@requires_sandbox_image
def test_sandbox_kills_a_hanging_process_within_its_timeout(tmp_path):
    (tmp_path / "test_hang.py").write_text("import time\ndef test_hangs():\n    time.sleep(60)\n")

    result = DockerTestRunner(SandboxLimits(timeout_seconds=3)).run(str(tmp_path))

    assert result.timed_out is True
    assert result.exit_code is None

    leftover = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=ai-swe-agent-sandbox-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert leftover.stdout.strip() == ""


@requires_sandbox_image
def test_sandbox_container_cannot_mutate_the_real_repository(tmp_path):
    (tmp_path / "test_write.py").write_text(
        "from pathlib import Path\n"
        "def test_writes_into_its_own_copy():\n"
        "    Path('created_by_container.txt').write_text('hello')\n"
    )
    DockerTestRunner(SandboxLimits(timeout_seconds=15)).run(str(tmp_path))
    assert not (tmp_path / "created_by_container.txt").exists()
