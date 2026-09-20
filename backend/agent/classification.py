"""Deterministic task-type classification.

Deliberately rule-based rather than LLM-based: a production system
might use the LLM here for richer intent understanding, but a
transparent, deterministic classifier keeps the graph's routing fully
testable without requiring a live LLM_API_KEY, and keeps the routing
decision itself inspectable (not hidden inside a model call) — in
keeping with this phase's instruction to expose safe execution
summaries rather than opaque reasoning.

Ties (a question containing both a "test" word and a "modify" word,
e.g. "add a test for the login function") are broken by checking test
keywords first, since "add a test" is a test request even though "add"
is also a modify keyword.
"""

import re

TEST_KEYWORDS = frozenset({"test", "tests", "testing"})
MODIFY_KEYWORDS = frozenset(
    {"fix", "change", "modify", "update", "add", "implement", "refactor", "remove", "delete", "rename", "create"}
)

_WORD_PATTERN = re.compile(r"\w+")


def classify_task(question: str) -> str:
    words = {w.lower() for w in _WORD_PATTERN.findall(question)}

    if words & TEST_KEYWORDS:
        return "test"
    if words & MODIFY_KEYWORDS:
        return "modify"
    return "answer"
