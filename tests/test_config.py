"""Tests for `backend/config.py` — the single source of truth for
"which environment variables are required vs optional", shared by
`backend/api/health.py` and `backend/scripts/check_production_config.py`.
"""

from config import check_environment, missing_required_settings


def test_database_url_is_the_only_required_setting():
    required = [s.name for s in check_environment() if s.required]
    assert required == ["DATABASE_URL"]


def test_check_environment_reports_configured_status_accurately(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@localhost/db")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    statuses = {s.name: s.configured for s in check_environment()}

    assert statuses["DATABASE_URL"] is True
    assert statuses["GITHUB_TOKEN"] is False


def test_missing_required_settings_lists_only_unset_required_ones(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert missing_required_settings() == ["DATABASE_URL"]

    monkeypatch.setenv("DATABASE_URL", "postgresql://user@localhost/db")
    assert missing_required_settings() == []


def test_check_environment_never_includes_actual_secret_values(monkeypatch):
    secret_value = "definitely-a-secret-value-12345"
    monkeypatch.setenv("DATABASE_URL", f"postgresql://user:{secret_value}@localhost/db")
    monkeypatch.setenv("GITHUB_TOKEN", secret_value)
    monkeypatch.setenv("LLM_API_KEY", secret_value)

    statuses = check_environment()

    for status in statuses:
        assert secret_value not in status.name
        assert secret_value not in status.description
        assert secret_value not in repr(status)


def test_every_setting_has_a_non_empty_description():
    for setting in check_environment():
        assert setting.description.strip()
