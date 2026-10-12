"""Unit tests for `api.trace_sanitizer` — what is scrubbed, and,
equally important, what must survive intact."""

import pytest

from api.trace_sanitizer import (
    REPOSITORY_ROOT_PLACEHOLDER,
    SERVER_PATH_PLACEHOLDER,
    known_roots_of,
    scrub_attributes,
    scrub_text,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("/etc/passwd", SERVER_PATH_PLACEHOLDER),
        ("failed at '/Users/alice/repo/x.py'", f"failed at '{SERVER_PATH_PLACEHOLDER}'"),
        ("cwd=/tmp/x and more", f"cwd={SERVER_PATH_PLACEHOLDER} and more"),
        ("src/app.py", "src/app.py"),
        ("https://github.com/octocat/Hello-World", "https://github.com/octocat/Hello-World"),
        ("Fix attempt 1/2 requires approval", "Fix attempt 1/2 requires approval"),
        ("and/or", "and/or"),
        ("./relative/path", "./relative/path"),
        ("~/home-relative", "~/home-relative"),
    ],
)
def test_scrub_text_without_known_roots(text, expected):
    assert scrub_text(text, []) == expected


def test_known_root_becomes_relative_or_placeholder():
    root = "/srv/repos/acme"
    assert scrub_text(f"{root}/src/a.py and {root}", [root]) == f"src/a.py and {REPOSITORY_ROOT_PLACEHOLDER}"


def test_secret_shaped_values_are_redacted_again():
    assert "ghp_" not in scrub_text("token ghp_" + "b" * 36, [])


def test_scrub_attributes_drops_path_keys_at_any_depth_and_keeps_others():
    result = scrub_attributes(
        {"root_path": "/a", "count": 3, "ok": True, "inner": [{"local_path": "/b", "relative": "x/y.py"}]}, []
    )
    assert result == {"count": 3, "ok": True, "inner": [{"relative": "x/y.py"}]}


@pytest.mark.parametrize("attrs, expected", [({"root_path": "/srv/r"}, ["/srv/r"]), ({"root_path": "/"}, []),
                                             ({"root_path": "rel"}, []), ({}, []), (None, [])])
def test_known_roots_only_accepts_real_absolute_roots(attrs, expected):
    assert known_roots_of(attrs) == expected
