"""Unified diff generation — standard library `difflib`, no dependency
needed: it's the same format `git diff`/`patch` use, and generating a
diff between two known strings is exactly what it's designed for.
"""

import difflib


def generate_unified_diff(original: str, proposed: str, relative_path: str) -> str:
    original_lines = original.splitlines(keepends=True)
    proposed_lines = proposed.splitlines(keepends=True)

    diff_lines = difflib.unified_diff(
        original_lines,
        proposed_lines,
        fromfile=f"a/{relative_path}",
        tofile=f"b/{relative_path}",
    )
    return "".join(diff_lines)
