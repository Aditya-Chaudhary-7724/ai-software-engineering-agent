from rag.context import build_context
from rag.models import Candidate


def _candidate(relative_path, content, start_line=1, end_line=1) -> Candidate:
    return Candidate(
        chunk_id=1,
        relative_path=relative_path,
        language="Python",
        chunk_type="function",
        symbol_name="foo",
        qualified_name="foo",
        start_line=start_line,
        end_line=end_line,
        content=content,
        vector_similarity=1.0,
        keyword_score=0.0,
        combined_score=1.0,
    )


def test_build_context_includes_file_header_and_content():
    text, included = build_context([_candidate("main.py", "def foo(): pass")], max_chars=1000)

    assert "main.py:1-1" in text
    assert "def foo(): pass" in text
    assert len(included) == 1


def test_build_context_empty_candidates_returns_empty():
    text, included = build_context([], max_chars=1000)

    assert text == ""
    assert included == []


def test_build_context_respects_max_chars_budget():
    candidates = [_candidate(f"file{i}.py", "x" * 100) for i in range(5)]

    text, included = build_context(candidates, max_chars=250)

    assert len(included) < 5
    assert len(text) <= 250 + len(included) * 50  # header overhead allowance


def test_build_context_always_includes_at_least_one_even_if_oversized():
    candidates = [_candidate("huge.py", "x" * 10000)]

    text, included = build_context(candidates, max_chars=100)

    assert len(included) == 1
    assert "huge.py" in text
