from pathlib import Path

import pytest

from ingestion.filters import FilterConfig, classify_file, is_ignored_directory, is_sensitive_filename


@pytest.mark.parametrize(
    "dir_name", ["node_modules", "__pycache__", ".git", ".venv", "dist", "build"]
)
def test_is_ignored_directory_true_for_known_names(dir_name):
    assert is_ignored_directory(dir_name, FilterConfig())


def test_is_ignored_directory_false_for_source_directory():
    assert not is_ignored_directory("src", FilterConfig())


def test_classify_file_relevant_text_file(tmp_path):
    file_path = tmp_path / "main.py"
    file_path.write_text("print('hello')\n")

    is_relevant, reason = classify_file(file_path, "main.py", file_path.stat().st_size, FilterConfig())

    assert is_relevant is True
    assert reason is None


def test_classify_file_ignores_known_binary_extension(tmp_path):
    file_path = tmp_path / "logo.png"
    file_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    is_relevant, reason = classify_file(file_path, "logo.png", file_path.stat().st_size, FilterConfig())

    assert is_relevant is False
    assert reason == "binary_extension"


def test_classify_file_ignores_generated_artifact_pattern(tmp_path):
    file_path = tmp_path / "bundle.min.js"
    file_path.write_text("(function(){})();")

    is_relevant, reason = classify_file(
        file_path, "bundle.min.js", file_path.stat().st_size, FilterConfig()
    )

    assert is_relevant is False
    assert reason == "generated_artifact"


def test_classify_file_ignores_oversized_file(tmp_path):
    file_path = tmp_path / "data.txt"
    file_path.write_text("x" * 100)
    config = FilterConfig(max_file_size_bytes=10)

    is_relevant, reason = classify_file(file_path, "data.txt", file_path.stat().st_size, config)

    assert is_relevant is False
    assert reason == "file_too_large"


def test_classify_file_size_limit_is_configurable(tmp_path):
    file_path = tmp_path / "data.txt"
    file_path.write_text("x" * 100)

    lenient_config = FilterConfig(max_file_size_bytes=1000)
    is_relevant, reason = classify_file(
        file_path, "data.txt", file_path.stat().st_size, lenient_config
    )

    assert is_relevant is True
    assert reason is None


def test_classify_file_sniffs_unknown_extension_with_binary_content(tmp_path):
    file_path = tmp_path / "payload.dat"
    file_path.write_bytes(b"\x00\x01\x02binary")

    is_relevant, reason = classify_file(
        file_path, "payload.dat", file_path.stat().st_size, FilterConfig()
    )

    assert is_relevant is False
    assert reason == "binary_content"


def test_classify_file_does_not_blindly_ignore_unknown_text_extension(tmp_path):
    file_path = tmp_path / "notes.xyz"
    file_path.write_text("just plain text content")

    is_relevant, reason = classify_file(
        file_path, "notes.xyz", file_path.stat().st_size, FilterConfig()
    )

    assert is_relevant is True
    assert reason is None


# Sensitive-filename matching is case-insensitive (security fix found in
# frontend phase F1): `fnmatch` is case-sensitive on POSIX, so `.ENV` or
# `ID_RSA` used to be classified as ordinary files and ingested.

SENSITIVE_CASE_VARIANTS = [
    ".ENV", ".Env", ".ENV.LOCAL", ".Env.Production", "ID_RSA", "Id_Dsa", "ID_ECDSA", "Id_Ed25519",
    "Server.PEM", "PRIVATE.KEY", "Cert.PFX", "Bundle.P12", "Release.KEYSTORE", "App.JKS",
    "Credentials.JSON", "SECRETS.JSON", ".NPMRC", ".NetRC", "Vault.KDBX",
]


@pytest.mark.parametrize("filename", SENSITIVE_CASE_VARIANTS)
def test_case_variants_of_sensitive_filenames_are_sensitive(filename):
    assert is_sensitive_filename(filename) is True


@pytest.mark.parametrize("filename", SENSITIVE_CASE_VARIANTS)
def test_classify_file_ignores_case_variant_sensitive_files(tmp_path, filename):
    file_path = tmp_path / filename
    file_path.write_text("SECRET=value\n")

    is_relevant, reason = classify_file(file_path, filename, file_path.stat().st_size, FilterConfig())

    assert is_relevant is False
    assert reason == "sensitive_file"


@pytest.mark.parametrize(
    "filename",
    [
        "Main.PY", "README.MD", "ID_RSA.PUB", "id_rsa.pub", "Environment.py", "env.py", "ENV",
        "dotenv_loader.py", "Keys.md", "monkey.py", "KeyStore.java", "Pembroke.txt",
        "Credentials.py", "credentials_test.json", "secrets_manager.py", "NetRC.md",
    ],
)
def test_case_insensitive_matching_does_not_broaden_to_ordinary_files(filename):
    assert is_sensitive_filename(filename) is False


def test_custom_mixed_case_patterns_also_match_case_insensitively():
    config = FilterConfig(sensitive_file_patterns=("*.Secret", "Deploy_Token.TXT"))
    assert is_sensitive_filename("a.SECRET", config) is True
    assert is_sensitive_filename("deploy_token.txt", config) is True
    assert is_sensitive_filename("deploy_token.md", config) is False


def test_an_empty_pattern_list_still_disables_the_check_for_every_case():
    assert is_sensitive_filename(".ENV", FilterConfig(sensitive_file_patterns=())) is False
