"""Resource limits / DoS (Phase 14, section 11): audits and re-verifies
the bounds that ALREADY exist across earlier phases (this section is
mostly confirmation, not new controls — see docs/security.md for which
limits were found missing and fixed vs. which were already present).
"""

import pytest
from pydantic import ValidationError

from ingestion.filters import DEFAULT_MAX_FILE_SIZE_BYTES
from ingestion.service import IngestionService

from tools.file_tools import READ_FILE_MAX_CHARS
from tools.schemas import SearchCodeInput

from rag.context import DEFAULT_MAX_CONTEXT_CHARS

from agent.state import MAX_FIX_ITERATIONS, MAX_LOOP_SECONDS, MAX_RETRIES

from sandbox.models import DEFAULT_CPU_LIMIT, DEFAULT_MEMORY_LIMIT, DEFAULT_PIDS_LIMIT, DEFAULT_TIMEOUT_SECONDS


def test_ingestion_rejects_oversized_files(tmp_path):
    oversized = tmp_path / "huge.py"
    oversized.write_bytes(b"#" + b"x" * (DEFAULT_MAX_FILE_SIZE_BYTES + 1))
    (tmp_path / "normal.py").write_text("def x(): pass\n")

    result = IngestionService().ingest(str(tmp_path))

    relevant = {f.relative_path for f in result.files}
    assert "huge.py" not in relevant
    assert "normal.py" in relevant
    reasons = {f.relative_path: f.reason for f in result.ignored_files}
    assert reasons["huge.py"] == "file_too_large"


def test_search_code_tool_bounds_top_k_against_unbounded_result_sets():
    with pytest.raises(ValidationError):
        SearchCodeInput(repository_id=1, query="x", top_k=10_000)

    with pytest.raises(ValidationError):
        SearchCodeInput(repository_id=1, query="x", top_k=0)

    SearchCodeInput(repository_id=1, query="x", top_k=50)  # the documented max is allowed


def test_read_file_max_chars_constant_is_a_real_bound():
    assert READ_FILE_MAX_CHARS > 0
    assert READ_FILE_MAX_CHARS <= DEFAULT_MAX_FILE_SIZE_BYTES  # never larger than the ingestion size cap itself


def test_rag_context_assembly_is_bounded():
    assert DEFAULT_MAX_CONTEXT_CHARS > 0
    assert DEFAULT_MAX_CONTEXT_CHARS < 100_000  # sane upper bound, not "basically unbounded"


def test_agent_retry_and_fix_loop_bounds_are_finite_and_small():
    assert 0 < MAX_RETRIES <= 5
    assert 0 < MAX_FIX_ITERATIONS <= 5
    assert 0 < MAX_LOOP_SECONDS <= 3600  # an hour is already generous; this catches an accidental removal of the bound


def test_sandbox_resource_limits_are_all_finite_and_nonzero():
    assert DEFAULT_TIMEOUT_SECONDS > 0
    assert DEFAULT_CPU_LIMIT > 0
    assert DEFAULT_PIDS_LIMIT > 0
    assert DEFAULT_MEMORY_LIMIT  # a non-empty string like "512m", not unset


def test_graph_expansion_is_bounded_to_one_hop_by_construction():
    """Confirms the bound is structural (the function's own body only
    ever queries one hop out), not a runtime parameter that could be
    silently widened — a static/textual check, deliberately blunt.
    """
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[2] / "backend" / "hybrid" / "graph_expansion.py"
    text = path.read_text()
    # Every real traversal pattern in this file uses a fixed, small hop
    # count (e.g. `*1` for inheritance ancestors is Phase 5's own graph,
    # not this file) — this file itself never issues a query with an
    # unbounded `*` (variable-length path) traversal.
    assert "MATCH" in text  # sanity: the file actually contains Cypher
    for line in text.splitlines():
        if "MATCH" in line and "*" in line:
            assert "*1" in line or "*0" in line, f"potentially unbounded traversal: {line.strip()}"
