"""Recursive file discovery.

Responsible only for answering "what files exist and where" — it does
not decide relevance (see filters.py) and does not read file contents.

Safety/robustness policy:
- Ignored directories (by name) are pruned during traversal, so we
  never descend into e.g. node_modules at all.
- Symlinks (files or directories) are never followed and never
  reported as discovered files, to avoid escaping the repository root
  or resolving broken/dangerous links.
- Inaccessible directories/files (permission errors, etc.) are
  recorded as non-fatal errors and traversal continues.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from ingestion.filters import is_ignored_directory, FilterConfig


@dataclass(frozen=True)
class DiscoveredFile:
    """A file found during traversal, before any relevance filtering."""

    absolute_path: Path
    relative_path: str


def discover_files(
    root: Path, config: FilterConfig, errors: list[str]
) -> list[DiscoveredFile]:
    """Recursively discover files under `root`, pruning ignored directories.

    Appends human-readable messages to `errors` for any inaccessible
    path encountered, but does not raise — traversal continues.
    """
    discovered: list[DiscoveredFile] = []

    def on_walk_error(os_err: OSError) -> None:
        errors.append(f"Cannot access '{os_err.filename}': {os_err.strerror}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_walk_error, followlinks=False):
        current_dir = Path(dirpath)

        # Prune ignored and symlinked directories in place so os.walk
        # never descends into them.
        dirnames[:] = [
            name
            for name in dirnames
            if not is_ignored_directory(name, config)
            and not (current_dir / name).is_symlink()
        ]

        for filename in filenames:
            file_path = current_dir / filename

            if file_path.is_symlink():
                continue

            try:
                relative_path = file_path.relative_to(root).as_posix()
            except ValueError:
                errors.append(f"Cannot compute relative path for '{file_path}'")
                continue

            discovered.append(DiscoveredFile(absolute_path=file_path, relative_path=relative_path))

    return discovered
