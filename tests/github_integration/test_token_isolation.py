"""Cross-cutting security test: proves GITHUB_TOKEN never reaches the
Phase 10 Docker sandbox, even when it is set in the host environment
that the sandbox's subprocess call would otherwise inherit. The sandbox
already passes zero `-e`/`--env-file` flags by construction (see
`backend/sandbox/docker_runner.py` and its own tests) — this test ties
that guarantee directly to the specific secret this phase introduces,
satisfying "never expose the GitHub token to repository code or Docker
sandbox containers" as a verified property, not just a design intent.
"""

import subprocess

from sandbox.docker_runner import DockerTestRunner


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_github_token_never_appears_in_sandbox_docker_command(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "definitely-a-secret-github-token")
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert True\n")

    captured = {}

    def _fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "info"]:
            return _FakeCompletedProcess(returncode=0)
        captured["cmd"] = cmd
        return _FakeCompletedProcess(returncode=0, stdout="1 passed")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    DockerTestRunner().run(str(tmp_path))

    cmd_str = " ".join(captured["cmd"])
    assert "definitely-a-secret-github-token" not in cmd_str
    assert "-e" not in captured["cmd"]
    assert "--env-file" not in captured["cmd"]
