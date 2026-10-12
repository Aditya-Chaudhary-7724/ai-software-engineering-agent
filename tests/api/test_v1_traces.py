"""Trace list/detail endpoints: UUID validation, path scrubbing, secret
re-redaction, and corrupt-file handling. Trace files are written in the
exact format `JSONFileRecorder` produces (via the recorder itself)."""

import json
import os
import uuid

import pytest

from api.dependencies import get_trace_directory
from api.v1 import API_V1_PREFIX
from observability.models import Span, SpanEvent, Trace
from observability.recorder import JSONFileRecorder

from tests.api.conftest import auth_headers

ROOT = "/private/var/folders/xy/T/eval-mod-approval-abc123"


def _trace(trace_id: str, status: str = "completed") -> Trace:
    root_span = Span(span_id="s0", trace_id=trace_id, parent_span_id=None, name="agent_run", kind="agent", status="ok",
                     start_time="2026-10-10T10:00:00+00:00", end_time="2026-10-10T10:00:01+00:00", duration_ms=1000.0)
    test_span = Span(
        span_id="s1", trace_id=trace_id, parent_span_id="s0", name="run_tests_after_apply", kind="sandbox", status="error",
        start_time="2026-10-10T10:00:00+00:00", end_time="2026-10-10T10:00:01+00:00", duration_ms=12.5,
        attributes={
            "test_ran": False,
            "proposed_relative_path": "src/app.py",
            "workspace_root": "/srv/workspaces/acme",
            "nested": {"root_path": ROOT, "note": f"copied {ROOT}/src/app.py to /tmp/sandbox-xyz/app.py"},
            "github_token": "ghp_" + "a" * 36,
        },
        error=f"NoTestCommandError: No supported test command could be detected for '{ROOT}'",
        events=[SpanEvent(timestamp="2026-10-10T10:00:00+00:00", name="retry",
                          attributes={"detail": "see /etc/hosts and https://github.com/octocat/Hello-World; attempt 1/2"})],
    )
    return Trace(
        trace_id=trace_id, name="agent_thread", started_at="2026-10-10T10:00:00+00:00",
        ended_at="2026-10-10T10:00:01+00:00", duration_ms=1000.0, status=status,
        attributes={"question": f"Fix the greet function in {ROOT}/src/app.py", "repository_id": 7, "root_path": ROOT},
        spans=[root_span, test_span],
    )


@pytest.fixture
def trace_dir(app, tmp_path):
    directory = tmp_path / "traces"
    directory.mkdir()
    app.dependency_overrides[get_trace_directory] = lambda: directory
    return directory


def _write(directory, trace_id: str, mtime: int, status: str = "completed") -> None:
    JSONFileRecorder(directory).record_trace(_trace(trace_id, status))
    os.utime(directory / f"{trace_id}.json", (mtime, mtime))


def _all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _all_strings(v)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)


def test_list_is_newest_first_paginated_and_skips_non_uuid_files(api_client, trace_dir):
    ids = [str(uuid.uuid4()) for _ in range(3)]
    for offset, trace_id in enumerate(ids):
        _write(trace_dir, trace_id, mtime=1_700_000_000 + offset)
    (trace_dir / "not-a-uuid.json").write_text("{}")
    # A distinct UUID: on a case-insensitive filesystem, ids[0].upper() would
    # overwrite ids[0] itself.
    (trace_dir / f"{str(uuid.uuid4()).upper()}.json").write_text("{}")

    first = api_client.get(f"{API_V1_PREFIX}/traces", params={"limit": 2}, headers=auth_headers()).json()
    second = api_client.get(f"{API_V1_PREFIX}/traces", params={"limit": 2, "offset": 2}, headers=auth_headers()).json()

    assert first["total"] == 3
    assert [t["trace_id"] for t in first["traces"]] == [ids[2], ids[1]]
    assert [t["trace_id"] for t in second["traces"]] == [ids[0]]
    summary = first["traces"][0]
    assert (summary["repository_id"], summary["span_count"], summary["error_span_count"]) == (7, 2, 1)
    assert summary["question"] == "Fix the greet function in src/app.py"


def test_empty_or_missing_trace_directory_is_an_empty_list(api_client, app, tmp_path):
    app.dependency_overrides[get_trace_directory] = lambda: tmp_path / "does-not-exist"
    body = api_client.get(f"{API_V1_PREFIX}/traces", headers=auth_headers()).json()
    assert body == {"traces": [], "total": 0, "limit": 25, "offset": 0}
    assert not (tmp_path / "does-not-exist").exists()


def test_detail_removes_every_server_path_and_redacts_secrets(api_client, trace_dir):
    trace_id = str(uuid.uuid4())
    _write(trace_dir, trace_id, mtime=1_700_000_000)

    response = api_client.get(f"{API_V1_PREFIX}/traces/{trace_id}", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    for text in _all_strings(body):
        assert "/private/var" not in text
        assert "/srv/workspaces" not in text
        assert "/tmp/sandbox" not in text
        assert "/etc/hosts" not in text
        assert "ghp_" not in text
        assert text not in ("root_path", "workspace_root")

    span = body["spans"][1]
    assert span["error"] == "NoTestCommandError: No supported test command could be detected for '[repository-root]'"
    assert span["attributes"]["proposed_relative_path"] == "src/app.py"
    assert span["attributes"]["nested"]["note"] == "copied src/app.py to [server-path]"
    # Relative paths, URLs, and ratios survive scrubbing intact.
    assert span["events"][0]["attributes"]["detail"] == (
        "see [server-path] and https://github.com/octocat/Hello-World; attempt 1/2"
    )
    assert body["attributes"] == {"question": "Fix the greet function in src/app.py", "repository_id": 7}


@pytest.mark.parametrize(
    "bad_id",
    ["not-a-uuid", "..%2F..%2Fetc%2Fpasswd", "..", "12345", "{%s}", "urn:uuid:%s", "UPPER", "%s.json", "%s%%00"],
)
def test_invalid_trace_ids_are_rejected_before_filesystem_access(api_client, trace_dir, bad_id):
    real_id = str(uuid.uuid4())
    _write(trace_dir, real_id, mtime=1_700_000_000)
    bad = real_id.upper() if bad_id == "UPPER" else (bad_id % real_id if "%s" in bad_id else bad_id)

    response = api_client.get(f"{API_V1_PREFIX}/traces/{bad}", headers=auth_headers())

    assert response.status_code in (400, 404)
    if response.status_code == 400:
        assert response.json() == {"error": "invalid_trace_id"}
    assert str(trace_dir) not in response.text


def test_unknown_trace_is_404(api_client, trace_dir):
    response = api_client.get(f"{API_V1_PREFIX}/traces/{uuid.uuid4()}", headers=auth_headers())
    assert (response.status_code, response.json()) == (404, {"error": "trace_not_found"})


def test_corrupt_trace_file_is_a_generic_error_and_does_not_break_the_list(api_client, trace_dir):
    good, corrupt = str(uuid.uuid4()), str(uuid.uuid4())
    _write(trace_dir, good, mtime=1_700_000_000)
    (trace_dir / f"{corrupt}.json").write_text('{"trace_id": ' + json.dumps(corrupt) + ', "spans": [{"bad": tru')

    detail = api_client.get(f"{API_V1_PREFIX}/traces/{corrupt}", headers=auth_headers())
    listing = api_client.get(f"{API_V1_PREFIX}/traces", headers=auth_headers()).json()

    assert (detail.status_code, detail.json()) == (500, {"error": "trace_unreadable"})
    assert [t["trace_id"] for t in listing["traces"]] == [good]
