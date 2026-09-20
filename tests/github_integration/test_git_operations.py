"""Unit tests for GitOperations with `subprocess.run` mocked out — no
real Docker or network involved (see test_github_integration.py for a
real `git clone` against a real public repository). The most important
property tested here: GITHUB_TOKEN must NEVER appear in any subprocess
argv list, only ever in the child process's environment.
"""

import subprocess

import pytest

from github_integration.exceptions import GitOperationError
from github_integration.git_operations import GitOperations


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_clone_without_token_passes_no_credential_helper(tmp_path, monkeypatch):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    GitOperations().clone("https://github.com/octocat/Hello-World.git", tmp_path / "dest")

    cmd = captured["cmd"]
    assert cmd == ["git", "clone", "https://github.com/octocat/Hello-World.git", str(tmp_path / "dest")]
    assert "credential.helper" not in " ".join(cmd)
    assert "GIT_TOKEN" not in captured["env"]


def test_clone_with_token_never_puts_token_in_argv(tmp_path, monkeypatch):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    GitOperations().clone(
        "https://github.com/octocat/Private-Repo.git", tmp_path / "dest", token="super-secret-token-value"
    )

    cmd = captured["cmd"]
    assert "super-secret-token-value" not in " ".join(cmd)
    assert captured["env"]["GIT_TOKEN"] == "super-secret-token-value"
    # git itself resolves $GIT_TOKEN from the environment at credential
    # time — the argv only ever references the variable NAME.
    assert any("$GIT_TOKEN" in part for part in cmd)


def test_push_uses_credential_helper_and_never_leaks_token(tmp_path, monkeypatch):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        captured["cwd"] = kwargs.get("cwd")
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    GitOperations().push(tmp_path, "fix-branch", token="another-secret")

    cmd = captured["cmd"]
    assert "another-secret" not in " ".join(cmd)
    assert captured["env"]["GIT_TOKEN"] == "another-secret"
    assert cmd[-2:] == ["origin", "fix-branch"]
    assert captured["cwd"] == tmp_path


def test_git_terminal_prompt_is_always_disabled(tmp_path, monkeypatch):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    GitOperations().create_branch(tmp_path, "new-branch")

    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_create_branch_builds_expected_command(tmp_path, monkeypatch):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    GitOperations().create_branch(tmp_path, "fix/greet")

    assert captured["cmd"] == ["git", "checkout", "-b", "fix/greet"]
    assert captured["cwd"] == tmp_path


def test_commit_all_scopes_author_identity_to_this_commit_only(tmp_path, monkeypatch):
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "rev-parse"]:
            return _FakeCompletedProcess(returncode=0, stdout="abc123deadbeef\n")
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    sha = GitOperations().commit_all(tmp_path, "Fix the bug", author_name="Test Bot", author_email="bot@example.com")

    assert sha == "abc123deadbeef"
    commit_call = next(c for c in calls if "commit" in c)
    assert "user.name=Test Bot" in commit_call
    assert "user.email=bot@example.com" in commit_call
    assert "-m" in commit_call and "Fix the bug" in commit_call


def test_a_failing_git_command_raises_git_operation_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _FakeCompletedProcess(returncode=1, stderr="fatal: not a git repository")
    )

    with pytest.raises(GitOperationError):
        GitOperations().create_branch(tmp_path, "new-branch")


def test_a_timing_out_git_command_raises_git_operation_error(tmp_path, monkeypatch):
    def _fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(GitOperationError):
        GitOperations().clone("https://github.com/octocat/Hello-World.git", tmp_path / "dest")
