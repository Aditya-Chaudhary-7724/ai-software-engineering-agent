"""Manual, human-readable demonstration of Phase 1 ingestion.

Builds a small synthetic sample repository in a temporary directory
(never the real project repository), runs IngestionService against it,
and prints a summary of what was discovered, ignored, and detected.

Run from the repository root:

    backend/.venv/bin/python backend/scripts/manual_ingestion_demo.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.service import IngestionService


def build_sample_repository(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("def main():\n    print('hello')\n")
    (root / "src" / "utils.js").write_text("function add(a, b) { return a + b; }\n")
    (root / "index.html").write_text("<!doctype html><html></html>\n")
    (root / "style.css").write_text("body { margin: 0; }\n")
    (root / "config.json").write_text('{"debug": true}\n')
    (root / "README.md").write_text("# Sample repository\n")

    (root / "node_modules" / "some-package").mkdir(parents=True)
    (root / "node_modules" / "some-package" / "index.js").write_text("module.exports = {};\n")

    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")

    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nsample-binary-content")
    (root / "compiled.pyc").write_bytes(b"\x00compiled")
    (root / "huge_generated_file.txt").write_text("x" * 2_000_000)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ingestion-demo-") as tmp_dir:
        root = Path(tmp_dir)
        build_sample_repository(root)

        result = IngestionService().ingest(str(root))

        print(f"Repository: {result.repository.name}")
        print(f"Root path: {result.repository.root_path}")
        print(f"Total files discovered: {result.repository.total_files_discovered}")
        print(f"Relevant files: {result.repository.relevant_files}")
        print(f"Ignored files: {result.repository.ignored_files}")
        print()

        print("Relevant files:")
        for file_meta in sorted(result.files, key=lambda f: f.relative_path):
            print(
                f"  {file_meta.relative_path:30s} "
                f"language={file_meta.language or '-':12s} "
                f"category={file_meta.category:12s} "
                f"size={file_meta.size_bytes}B"
            )
        print()

        print("Ignored files:")
        for ignored in sorted(result.ignored_files, key=lambda f: f.relative_path):
            print(f"  {ignored.relative_path:30s} reason={ignored.reason}")
        print()

        print("Language statistics:")
        for language, stats in sorted(result.repository.language_stats.items()):
            print(f"  {language:12s} files={stats.file_count:3d} bytes={stats.total_bytes}")

        if result.errors:
            print()
            print("Errors encountered:")
            for error in result.errors:
                print(f"  {error}")


if __name__ == "__main__":
    main()
