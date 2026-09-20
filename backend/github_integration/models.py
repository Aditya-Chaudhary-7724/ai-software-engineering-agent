"""Plain, dependency-free data models for the GitHub integration
subsystem. Same rationale as every other phase's dataclasses: no API
boundary exists yet to justify a validation library here.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RepositoryReference:
    """A validated `owner/repo` pair, produced only by
    `url_validation.parse_github_url` — never constructed from
    unvalidated input elsewhere in this package.
    """

    owner: str
    repo: str
    url: str  # normalized https://github.com/<owner>/<repo>, no .git suffix


@dataclass(frozen=True)
class RepositoryMetadata:
    owner: str
    name: str
    full_name: str
    description: Optional[str]
    default_branch: str
    clone_url: str
    private: bool
    stargazers_count: int


@dataclass(frozen=True)
class ClonedRepository:
    reference: RepositoryReference
    local_path: str
    default_branch: str


@dataclass(frozen=True)
class PullRequestResult:
    number: int
    html_url: str
    state: str
