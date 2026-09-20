"""GitHub integration security (Phase 14, section 8): adversarial
regression tests against the REAL `parse_github_url`, `GitOperations`
(mocked `subprocess.run` only where a real `git` call would need
network access or a writable remote — command construction is
verified against real argv lists, not simulated), and `resolve_clone_destination`.
"""

import subprocess

import pytest

from github_integration.exceptions import InvalidRepositoryURLError, WorkspaceError
from github_integration.git_operations import GitOperations
from github_integration.models import RepositoryReference
from github_integration.url_validation import parse_github_url
from github_integration.workspace import resolve_clone_destination


@pytest.mark.parametrize(
    "malicious_url",
    [
        "https://github.com/../../etc/passwd",
        "https://github.com/owner/../../etc",
        "https://github.com/owner/repo; rm -rf /",
        "https://github.com/owner/repo`whoami`",
        "https://github.com/owner/repo$(whoami)",
        "https://github.com/owner/repo\nGITHUB_TOKEN=leaked",
        "git@github.com:owner/repo.git",  # SSH form, out of scope by design
        "https://attacker.com/owner/repo",
        "https://github.com.attacker.com/owner/repo",
        "ftp://github.com/owner/repo",
        "javascript:alert(1)",
        "",
        "   ",
    ],
)
def test_url_validation_rejects_malicious_or_out_of_scope_urls(malicious_url):
    with pytest.raises(InvalidRepositoryURLError):
        parse_github_url(malicious_url)


def test_url_validation_never_lets_a_shell_metacharacter_through_in_owner_or_repo():
    """Even if some future caller ever passed owner/repo into a shell
    command (this codebase does not — git_operations.py uses argv
    lists, never shell=True), the validator's character-class
    restriction is a real, independent barrier: it rejects these
    BEFORE they could reach any such call site.
    """
    for shell_metachar in [";", "|", "&", "$", "`", "\n", "\r", " ", "'", '"']:
        with pytest.raises(InvalidRepositoryURLError):
            parse_github_url(f"https://github.com/owner/repo{shell_metachar}evil")


def test_workspace_confinement_rejects_a_reference_that_would_escape_even_if_validation_were_bypassed(tmp_path):
    """Defense in depth: resolve_clone_destination is exercised directly
    with an ALREADY-malicious reference (as if url_validation had a
    bug), proving the workspace boundary holds independently.
    """
    malicious_reference = RepositoryReference(owner="..", repo="..", url="https://github.com/../..")
    with pytest.raises(WorkspaceError):
        resolve_clone_destination(str(tmp_path), malicious_reference)


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_github_token_never_appears_in_any_constructed_git_argv(tmp_path, monkeypatch):
    """The concrete secret-leakage vector this section cares about:
    scanning EVERY argv list ever passed to subprocess.run during a
    clone+push cycle for the literal token value.
    """
    captured_commands = []
    secret_token = "ghp_averyrealsecrettokenvalue1234567890"

    def _fake_run(cmd, **kwargs):
        captured_commands.append(cmd)
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    ops = GitOperations()
    ops.clone("https://github.com/owner/private-repo.git", tmp_path / "dest", token=secret_token)
    ops.push(tmp_path / "dest", "fix-branch", token=secret_token)

    for cmd in captured_commands:
        assert secret_token not in " ".join(cmd), f"token leaked into argv: {cmd}"


def test_github_token_is_only_ever_in_the_child_process_environment_not_argv(tmp_path, monkeypatch):
    captured_envs = []

    def _fake_run(cmd, **kwargs):
        captured_envs.append(kwargs.get("env", {}))
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    GitOperations().push(tmp_path, "fix-branch", token="secret-value")

    assert any(env.get("GIT_TOKEN") == "secret-value" for env in captured_envs)


@pytest.mark.parametrize(
    "malicious_branch_name",
    ["--upload-pack=touch /tmp/pwned", "-oProxyCommand=touch /tmp/pwned", "--exec=whoami"],
)
def test_real_git_rejects_flag_injection_style_branch_names(tmp_path, malicious_branch_name):
    """Real, integrated test (no mocking of subprocess) — proves git's
    own ref-name validation rejects an argument-injection attempt
    against `git checkout -b <branch_name>`, rather than assuming it.
    """
    from github_integration.exceptions import GitOperationError

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "--allow-empty", "-q", "-m", "init"], check=True, capture_output=True)

    with pytest.raises(GitOperationError):
        GitOperations().create_branch(tmp_path, malicious_branch_name)


def test_git_terminal_prompt_is_disabled_so_a_credential_prompt_cannot_hang(tmp_path, monkeypatch):
    captured_envs = []

    def _fake_run(cmd, **kwargs):
        captured_envs.append(kwargs.get("env", {}))
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    GitOperations().create_branch(tmp_path, "some-branch")

    assert captured_envs[0].get("GIT_TERMINAL_PROMPT") == "0"
