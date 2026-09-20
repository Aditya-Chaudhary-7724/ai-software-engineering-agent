"""Manual, human-readable demonstration of Phase 10's Docker sandbox
test runner, standalone (not through the agent — see
manual_agent_demo.py for the full testing-loop integration).

Demonstrates, against real Docker containers using the
`ai-swe-agent-sandbox-python:latest` image:
1. A passing test suite.
2. A failing test suite (captured stdout/stderr/exit code, no crash).
3. A test that hangs — enforced timeout, container force-killed.
4. Network access is genuinely blocked (not just configured).
5. The root filesystem is genuinely read-only outside the workspace.
6. The container operates on a COPY of the repository — writes made by
   a test never reach the real directory on disk.

Everything happens inside a temporary directory that is deleted when
this script exits. Requires Docker Desktop running and the sandbox
image built:

    docker build -t ai-swe-agent-sandbox-python:latest \\
        -f backend/sandbox/docker/python-test.Dockerfile backend/sandbox/docker

Run from the repository root:

    .venv/bin/python backend/scripts/manual_sandbox_demo.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sandbox.docker_runner import DockerTestRunner
from sandbox.exceptions import DockerUnavailableError
from sandbox.models import SandboxLimits


def _print_result(label: str, result) -> None:
    print(f"--- {label} ---")
    print(f"passed={result.passed} exit_code={result.exit_code} timed_out={result.timed_out} "
          f"duration={result.duration_seconds:.2f}s")
    if result.stdout.strip():
        print(f"stdout:\n{result.stdout.strip()}")
    if result.stderr.strip():
        print(f"stderr:\n{result.stderr.strip()}")
    print()


def main() -> None:
    runner = DockerTestRunner(SandboxLimits(timeout_seconds=15))

    try:
        with tempfile.TemporaryDirectory(prefix="sandbox-demo-passing-") as tmp_dir:
            root = Path(tmp_dir)
            (root / "test_math.py").write_text("def test_addition():\n    assert 1 + 1 == 2\n")
            _print_result("1. Passing test suite", runner.run(str(root)))

        with tempfile.TemporaryDirectory(prefix="sandbox-demo-failing-") as tmp_dir:
            root = Path(tmp_dir)
            (root / "test_math.py").write_text("def test_addition():\n    assert 1 + 1 == 3\n")
            _print_result("2. Failing test suite", runner.run(str(root)))

        with tempfile.TemporaryDirectory(prefix="sandbox-demo-hang-") as tmp_dir:
            root = Path(tmp_dir)
            (root / "test_hang.py").write_text("import time\ndef test_hangs():\n    time.sleep(60)\n")
            hang_runner = DockerTestRunner(SandboxLimits(timeout_seconds=3))
            _print_result("3. Hanging test suite (3s timeout, container force-killed)", hang_runner.run(str(root)))

        with tempfile.TemporaryDirectory(prefix="sandbox-demo-network-") as tmp_dir:
            root = Path(tmp_dir)
            (root / "test_network.py").write_text(
                "import socket\n"
                "def test_network_is_blocked():\n"
                "    try:\n"
                "        socket.create_connection(('8.8.8.8', 53), timeout=3)\n"
                "        raise AssertionError('network should be blocked')\n"
                "    except OSError:\n"
                "        pass\n"
            )
            _print_result("4. Network access is genuinely blocked", runner.run(str(root)))

        with tempfile.TemporaryDirectory(prefix="sandbox-demo-readonly-") as tmp_dir:
            root = Path(tmp_dir)
            (root / "test_readonly.py").write_text(
                "def test_root_filesystem_is_read_only():\n"
                "    try:\n"
                "        open('/etc/pwned', 'w').write('x')\n"
                "        raise AssertionError('root filesystem should be read-only')\n"
                "    except OSError:\n"
                "        pass\n"
            )
            _print_result("5. Root filesystem is genuinely read-only", runner.run(str(root)))

        with tempfile.TemporaryDirectory(prefix="sandbox-demo-isolation-") as tmp_dir:
            root = Path(tmp_dir)
            (root / "test_write.py").write_text(
                "from pathlib import Path\n"
                "def test_writes_a_file():\n"
                "    Path('created_inside_container.txt').write_text('hello')\n"
                "    assert Path('created_inside_container.txt').exists()\n"
            )
            result = runner.run(str(root))
            _print_result("6. Container writes never reach the real repository", result)
            leaked = (root / "created_inside_container.txt").exists()
            print(f"File leaked onto the host repository: {leaked} (expected: False)")
    except DockerUnavailableError as exc:
        print(f"Docker is not available: {exc}")
        print("Start Docker Desktop and build the sandbox image, then re-run this script.")


if __name__ == "__main__":
    main()
