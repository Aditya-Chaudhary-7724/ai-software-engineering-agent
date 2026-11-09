"""Docker sandbox security (Phase 14, section 7): real, integrated
regression tests against actual containers via `DockerTestRunner` —
not mocked — proving each control Phase 10 already claims to have
actually holds, plus one NEW probe this phase adds (process/PID
exhaustion). Skips cleanly if Docker or the sandbox image isn't
available, per this project's "LOCAL TESTED vs REQUIRES EXTERNAL
SERVICE" convention.
"""

import subprocess
from pathlib import Path

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


# -- F8: untrusted code never runs as root ------------------------------------------


def test_sandbox_command_never_runs_as_root():
    from sandbox import docker_runner

    cmd = DockerTestRunner()._build_docker_command(Path("/tmp/workspace"), "c", ["true"], "some-image")
    user = cmd[cmd.index("--user") + 1]
    assert user == docker_runner.container_user()
    assert user.split(":")[0] != "0"


def test_container_user_falls_back_to_nobody_when_the_app_runs_as_root(monkeypatch):
    from sandbox import docker_runner

    monkeypatch.setattr(docker_runner.os, "getuid", lambda: 0)
    monkeypatch.setattr(docker_runner.os, "getgid", lambda: 0)
    assert docker_runner.container_user() == "65534:65534"


@requires_sandbox_image
def test_untrusted_code_runs_without_root_and_can_still_use_its_workspace(tmp_path):
    """Real container: the repository's own test asserts it is not uid 0,
    then writes into its workspace (pytest's cache and __pycache__ need it)."""
    (tmp_path / "test_identity.py").write_text(
        "import os\n"
        "def test_not_root_and_workspace_writable():\n"
        "    assert os.getuid() != 0, 'sandbox code must not run as root'\n"
        "    assert os.getgid() != 0\n"
        "    open('written-by-sandbox.txt', 'w').write('ok')\n"
    )
    result = DockerTestRunner(SandboxLimits(timeout_seconds=30)).run(str(tmp_path))
    assert result.passed is True, result.stdout + result.stderr
    assert not (tmp_path / "written-by-sandbox.txt").exists()  # it wrote to the COPY, never the real repo
