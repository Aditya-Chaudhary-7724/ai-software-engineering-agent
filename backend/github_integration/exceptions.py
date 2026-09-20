"""Exceptions raised by the GitHub integration subsystem."""


class GitHubIntegrationError(Exception):
    """Base class for all GitHub-integration-related errors."""


class InvalidRepositoryURLError(GitHubIntegrationError):
    """Raised when a repository URL isn't a well-formed
    `https://github.com/<owner>/<repo>` URL — see url_validation.py.
    """


class MissingCredentialsError(GitHubIntegrationError):
    """Raised when an operation that requires `GITHUB_TOKEN` (pushing,
    creating a pull request, or reading a private repository) is
    attempted without one configured.
    """


class WorkspaceError(GitHubIntegrationError):
    """Raised when a resolved clone destination would escape the
    configured workspace root — see workspace.py.
    """


class GitHubAPIError(GitHubIntegrationError):
    """Raised when the GitHub REST API returns an error response."""


class GitOperationError(GitHubIntegrationError):
    """Raised when a local `git` command fails."""


class UnauthorizedActionError(GitHubIntegrationError):
    """Raised when a consequential action (push, pull request) is
    attempted without the explicit authorization/approval flag that
    gates it — this project's human-approval-for-consequential-actions
    rule, applied to GitHub write operations specifically.
    """
