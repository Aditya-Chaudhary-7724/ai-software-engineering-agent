"""Pure unit tests — no network involved."""

import pytest

from github_integration.exceptions import InvalidRepositoryURLError
from github_integration.url_validation import parse_github_url


def test_parses_a_plain_https_url():
    ref = parse_github_url("https://github.com/octocat/Hello-World")
    assert ref.owner == "octocat"
    assert ref.repo == "Hello-World"
    assert ref.url == "https://github.com/octocat/Hello-World"


def test_strips_dot_git_suffix():
    ref = parse_github_url("https://github.com/octocat/Hello-World.git")
    assert ref.repo == "Hello-World"


def test_strips_trailing_slash():
    ref = parse_github_url("https://github.com/octocat/Hello-World/")
    assert ref.repo == "Hello-World"


def test_strips_surrounding_whitespace():
    ref = parse_github_url("  https://github.com/octocat/Hello-World  ")
    assert ref.owner == "octocat"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/octocat/Hello-World",  # not https
        "https://gitlab.com/octocat/Hello-World",  # wrong host
        "https://evilgithub.com/octocat/Hello-World",  # lookalike host
        "https://github.com.evil.com/octocat/Hello-World",  # lookalike host (suffix trick)
        "git@github.com:octocat/Hello-World.git",  # ssh form, out of scope
        "https://github.com/octocat",  # missing repo segment
        "https://github.com/octocat/Hello-World/extra",  # extra path segment
        "https://github.com/",  # empty path
        "https://user:pass@github.com/octocat/Hello-World",  # userinfo smuggled into netloc
        "https://github.com/../../etc/passwd",  # path traversal attempt
        "https://github.com/octocat/../../../etc",  # path traversal attempt
        "https://github.com/oct%2Focat/Hello-World",  # encoded slash smuggling
        "not-a-url-at-all",
        "",
        "   ",
    ],
)
def test_rejects_invalid_or_out_of_scope_urls(url):
    with pytest.raises(InvalidRepositoryURLError):
        parse_github_url(url)


def test_rejects_non_string_input():
    with pytest.raises(InvalidRepositoryURLError):
        parse_github_url(None)  # type: ignore[arg-type]
