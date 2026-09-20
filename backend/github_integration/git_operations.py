"""Local git operations via the `git` CLI over `subprocess` — the same
"shell out to the official CLI instead of adding an SDK" pattern as
Phase 10's Docker sandbox (`backend/sandbox/docker_runner.py`).

CRITICAL — never put GITHUB_TOKEN in a subprocess argv list: argv is
visible to any process on the same host (`ps`, `/proc/<pid>/cmdline`),
unlike an environment variable, which is only visible via
`/proc/<pid>/environ` and only to the same user (or root) — a
materially smaller exposure surface. Every method here that needs
authentication passes the token through the `GIT_TOKEN` environment
variable of the child process only, and configures git's credential
helper to read `$GIT_TOKEN` from that environment at request time. The
token itself never appears as a command-line argument, in a URL string,
in a log line, or on disk — verified directly in
`tests/github_integration/test_git_operations.py`.
"""

import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from github_integration.exceptions import GitOperationError

_GIT_COMMAND_TIMEOUT_SECONDS = 120

# Never let git block waiting for interactive credential input — an
# automated caller has no terminal to answer it, and this project must
# never accept credentials any way other than GITHUB_TOKEN.
_BASE_ENV_OVERRIDES = {"GIT_TERMINAL_PROMPT": "0"}

# A `credential.helper` value starting with `!` is run through a shell
# BY GIT ITSELF (documented git behavior, not something this module
# implements) — so the only thing that ever appears in this string, or
# in any subprocess argv, is the environment variable's NAME. The
# actual secret value only ever exists in the child process's
# environment (`$GIT_TOKEN`), read by that shell at the moment git asks
# for credentials.
_TOKEN_CREDENTIAL_HELPER = '!f() { echo username=x-access-token; echo "password=$GIT_TOKEN"; }; f'


def _run_git(args: List[str], cwd: Optional[Path] = None, token: Optional[str] = None) -> str:
    env: Dict[str, str] = {**os.environ, **_BASE_ENV_OVERRIDES}
    command = ["git"]
    if token:
        env["GIT_TOKEN"] = token
        command += ["-c", f"credential.helper={_TOKEN_CREDENTIAL_HELPER}"]
    command += args

    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, timeout=_GIT_COMMAND_TIMEOUT_SECONDS, env=env
        )
    except subprocess.TimeoutExpired as exc:
        raise GitOperationError(f"git {' '.join(args)} timed out after {_GIT_COMMAND_TIMEOUT_SECONDS}s.") from exc

    if result.returncode != 0:
        raise GitOperationError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


class GitOperations:
    """Stateless: every method takes the repository path (or clone
    target) explicitly rather than holding one open across calls, so a
    single instance can be reused for any number of repositories.
    """

    def clone(self, clone_url: str, destination: Path, token: Optional[str] = None) -> None:
        _run_git(["clone", clone_url, str(destination)], token=token)

    def create_branch(self, repo_path: Path, branch_name: str) -> None:
        _run_git(["checkout", "-b", branch_name], cwd=repo_path)

    def commit_all(self, repo_path: Path, message: str, author_name: str, author_email: str) -> str:
        """Stages every change and commits it. The author identity is
        scoped to this single `git commit` invocation via `-c` flags —
        it never touches this clone's persistent config, let alone this
        project's own git identity (a separate repository entirely).
        Returns the new commit SHA.
        """
        _run_git(["add", "-A"], cwd=repo_path)
        _run_git(
            ["-c", f"user.name={author_name}", "-c", f"user.email={author_email}", "commit", "-m", message],
            cwd=repo_path,
        )
        return _run_git(["rev-parse", "HEAD"], cwd=repo_path).strip()

    def push(self, repo_path: Path, branch_name: str, token: str, remote: str = "origin") -> None:
        _run_git(["push", remote, branch_name], cwd=repo_path, token=token)
