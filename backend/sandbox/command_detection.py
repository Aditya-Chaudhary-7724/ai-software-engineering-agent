"""Detects which test command (if any) applies to a repository.

Only Python repositories using pytest are supported in this phase.
Deliberately not extended to other ecosystems (e.g. `npm test`) yet:
this project's own dependency/scope policy is to add support only when
there's a real need, and every worked example, manual demo, and test
repository in this project so far is Python. Reuses Phase 1's
`IngestionService` for file discovery rather than re-implementing
directory traversal and ignore-pattern handling (`.git`, `.venv`,
`node_modules`, etc. are already handled there correctly).
"""

from pathlib import Path
from typing import List, Optional

from ingestion.service import IngestionService

_PYTEST_CONFIG_FILENAMES = ("pytest.ini", "setup.cfg", "pyproject.toml", "tox.ini")


def detect_test_command(root_path: str) -> Optional[List[str]]:
    root = Path(root_path)
    has_pytest_config = any((root / name).exists() for name in _PYTEST_CONFIG_FILENAMES)

    ingestion_result = IngestionService().ingest(root_path)
    has_python_test_files = any(
        file.language == "Python" and (file.filename.startswith("test_") or file.filename.endswith("_test.py"))
        for file in ingestion_result.files
    )

    if has_pytest_config or has_python_test_files:
        return ["python", "-m", "pytest", "-q"]
    return None
