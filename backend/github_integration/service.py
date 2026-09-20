"""Orchestrates the GitHub integration workflow behind one service:

    validate URL -> fetch metadata -> resolve a safe workspace destination
    -> clone -> [caller runs the existing Phase 1/2/3/5 pipeline against
    local_path — this service does not reimplement any of it]
    -> create branch -> commit -> [push ONLY if explicitly authorized]
    -> [open a pull request ONLY after human approval]

Mirrors this project's other service classes (`ModificationService`,
`AgentService`): every consequential action takes an explicit boolean
the caller must set after a real authorization/approval step upstream.
It is never inferred, defaulted to True, or silently assumed — the same
"human approval required for consequential actions" principle Phase 9
built for code modification, applied here to GitHub write operations.
"""

import os
from pathlib import Path
from typing import Optional

from github_integration.api_client import GitHubAPIClient
from github_integration.exceptions import MissingCredentialsError, UnauthorizedActionError
from github_integration.git_operations import GitOperations
from github_integration.models import ClonedRepository, PullRequestResult, RepositoryMetadata, RepositoryReference
from github_integration.url_validation import parse_github_url
from github_integration.workspace import resolve_clone_destination


class GitHubIntegrationService:
    def __init__(self, token: Optional[str] = None, git_operations: Optional[GitOperations] = None) -> None:
        # Authentication through GITHUB_TOKEN only: an explicit token
        # always wins (useful for tests), otherwise read from the
        # environment. Never hardcoded, never read from anywhere else.
        self._token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self._api_client = GitHubAPIClient(self._token)
        self._git = git_operations or GitOperations()

    def validate_repository_url(self, url: str) -> RepositoryReference:
        return parse_github_url(url)

    def get_repository_metadata(self, url: str) -> RepositoryMetadata:
        reference = parse_github_url(url)
        return self._api_client.get_repository(reference.owner, reference.repo)

    def clone_repository(self, url: str, workspace_root: str) -> ClonedRepository:
        """Validates the URL, resolves a safe destination under
        `workspace_root` (never outside it — see workspace.py), and
        clones. The caller is then expected to run the EXISTING Phase
        1/2/3/5 pipeline (`IngestionService`, `ParsingService`,
        `IndexingService`, `GraphBuilder`) against `.local_path` exactly
        as it would for any other local repository — see
        `github_integration/pipeline.py` for a convenience wrapper that
        does exactly this, unchanged.
        """
        reference = parse_github_url(url)
        metadata = self._api_client.get_repository(reference.owner, reference.repo)
        destination = resolve_clone_destination(workspace_root, reference)
        # A public repo clones anonymously either way; a private one
        # needs the token. Never send the token for a public repo —
        # nothing needs it, and it's one fewer place a mistake could
        # matter.
        self._git.clone(metadata.clone_url, destination, token=self._token if metadata.private else None)
        return ClonedRepository(
            reference=reference, local_path=str(destination), default_branch=metadata.default_branch
        )

    def create_branch(self, cloned: ClonedRepository, branch_name: str) -> None:
        self._git.create_branch(Path(cloned.local_path), branch_name)

    def commit_all(self, cloned: ClonedRepository, message: str, author_name: str, author_email: str) -> str:
        """`author_name`/`author_email` are required, not defaulted:
        this project must never invent or hardcode an identity to
        attribute a commit to (see CLAUDE.md's GitHub-identity rules) —
        the caller (a human, or a bot account THEY configured with its
        own token) always supplies it explicitly.
        """
        return self._git.commit_all(Path(cloned.local_path), message, author_name, author_email)

    def push_branch(self, cloned: ClonedRepository, branch_name: str, *, authorized: bool) -> None:
        if not authorized:
            raise UnauthorizedActionError(
                "Refusing to push: this requires explicit authorization (authorized=True) from a real "
                "upstream decision — it is never assumed."
            )
        if not self._token:
            raise MissingCredentialsError("GITHUB_TOKEN is required to push a branch.")
        self._git.push(Path(cloned.local_path), branch_name, token=self._token)

    def create_pull_request(
        self,
        cloned: ClonedRepository,
        head_branch: str,
        title: str,
        body: str,
        *,
        approved: bool,
        base_branch: Optional[str] = None,
    ) -> PullRequestResult:
        if not approved:
            raise UnauthorizedActionError(
                "Refusing to create a pull request: this requires explicit human approval (approved=True) "
                "— the same human-approval gate as Phase 9's apply_change."
            )
        base = base_branch or cloned.default_branch
        return self._api_client.create_pull_request(
            cloned.reference.owner, cloned.reference.repo, head=head_branch, base=base, title=title, body=body
        )
