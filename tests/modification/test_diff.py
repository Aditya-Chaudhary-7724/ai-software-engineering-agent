from modification.diff import generate_unified_diff


def test_generate_unified_diff_shows_change():
    diff = generate_unified_diff("def add(a, b):\n    return a\n", "def add(a, b):\n    return a + b\n", "math.py")

    assert "--- a/math.py" in diff
    assert "+++ b/math.py" in diff
    assert "-    return a" in diff
    assert "+    return a + b" in diff


def test_generate_unified_diff_identical_content_is_empty():
    content = "x = 1\n"
    diff = generate_unified_diff(content, content, "main.py")

    assert diff == ""
