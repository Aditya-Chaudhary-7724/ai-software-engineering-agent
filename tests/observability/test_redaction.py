"""Pure unit tests for redaction/sanitization — no service involved.

Includes a REGRESSION test for a real bug found while building this
phase: an earlier version of `_is_sensitive_key` did raw substring
matching, so `keyword_hit_count` was redacted purely because "key" is
a substring of "keyword" — verified directly against a real trace
produced by `manual_agent_demo.py` before being fixed here.
"""

from observability.redaction import REDACTED_MARKER, redact_text, sanitize_metadata


def test_redacts_common_credential_key_names():
    result = sanitize_metadata(
        {
            "api_key": "value",
            "API_KEY": "value",
            "GITHUB_TOKEN": "value",
            "password": "value",
            "db_password": "value",
            "Authorization": "value",
            "access_key_id": "value",
            "private_key": "value",
        }
    )
    assert all(v == REDACTED_MARKER for v in result.values())


def test_does_not_redact_keys_that_merely_contain_a_sensitive_substring():
    """The regression case: 'keyword' contains 'key' as a substring but
    is not a credential-shaped key name.
    """
    result = sanitize_metadata(
        {
            "keyword_hit_count": 3,
            "vector_hit_count": 2,
            "monkey_count": 1,
            "turkey_dinner": "not a secret",
            "llm_provider": "StubLLMProvider",
        }
    )
    assert result == {
        "keyword_hit_count": 3,
        "vector_hit_count": 2,
        "monkey_count": 1,
        "turkey_dinner": "not a secret",
        "llm_provider": "StubLLMProvider",
    }


def test_redacts_camel_case_credential_keys():
    result = sanitize_metadata({"apiKey": "value", "accessToken": "value"})
    assert result["apiKey"] == REDACTED_MARKER
    assert result["accessToken"] == REDACTED_MARKER


def test_redacts_github_token_shaped_value_under_an_innocuous_key():
    result = sanitize_metadata({"message": "failed using ghp_abcdefghijklmnopqrstuvwxyz0123"})
    assert "ghp_" not in result["message"]
    assert REDACTED_MARKER in result["message"]


def test_redacts_bearer_header_shaped_value():
    result = sanitize_metadata({"header": "Authorization: Bearer sometoken1234567890"})
    assert "sometoken1234567890" not in result["header"]


def test_redacts_openai_style_secret_key_value():
    result = sanitize_metadata({"note": "key was sk-abcdefghijklmnopqrstuvwx"})
    assert "sk-abcdefghijklmnopqrstuvwx" not in result["note"]


def test_redacts_database_url_with_embedded_password():
    result = sanitize_metadata({"db": "postgresql://user:hunter2@localhost:5432/mydb"})
    assert "hunter2" not in result["db"]


def test_truncates_long_strings_instead_of_storing_full_source_code():
    long_text = "x" * 10_000
    result = sanitize_metadata({"content": long_text})
    assert len(result["content"]) < len(long_text)
    assert "truncated" in result["content"]


def test_recursively_sanitizes_nested_dicts():
    result = sanitize_metadata({"outer": {"api_key": "value", "count": 5}})
    assert result["outer"]["api_key"] == REDACTED_MARKER
    assert result["outer"]["count"] == 5


def test_sanitizes_items_within_lists():
    result = sanitize_metadata({"items": [{"password": "x"}, "plain string", 42]})
    assert result["items"][0]["password"] == REDACTED_MARKER
    assert result["items"][1] == "plain string"
    assert result["items"][2] == 42


def test_bounds_list_length():
    result = sanitize_metadata({"items": list(range(1000))})
    assert len(result["items"]) <= 20


def test_never_raises_on_malformed_input():
    class Unrepresentable:
        def __str__(self):
            raise RuntimeError("boom")

    result = sanitize_metadata({"weird": Unrepresentable(), 123: "numeric key"})
    assert "weird" in result
    assert "123" in result  # non-string keys are coerced to strings, never dropped or crashed on


def test_non_dict_input_returns_empty_dict_rather_than_raising():
    assert sanitize_metadata(None) == {}  # type: ignore[arg-type]
    assert sanitize_metadata("not a dict") == {}  # type: ignore[arg-type]


def test_redact_text_never_raises():
    assert isinstance(redact_text("plain text"), str)
