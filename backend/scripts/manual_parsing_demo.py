"""Manual, human-readable demonstration of Phase 2 parsing.

Builds a small multi-language synthetic repository in a temporary
directory, runs Phase 1 ingestion followed by Phase 2 parsing, and
prints the extracted symbols per file.

Run from the repository root:

    .venv/bin/python backend/scripts/manual_parsing_demo.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.service import IngestionService
from parsing.service import ParsingService


def build_sample_repository(root: Path) -> None:
    (root / "animals.py").write_text(
        "class Animal:\n"
        "    def speak(self):\n"
        "        return '...'\n\n"
        "class Dog(Animal):\n"
        "    def speak(self):\n"
        "        return 'Woof'\n"
    )
    (root / "app.js").write_text(
        "import { readFile } from 'fs';\n\n"
        "function add(a, b) {\n"
        "    return a + b;\n"
        "}\n"
    )
    (root / "Server.go").write_text(
        "package main\n\n"
        "import \"fmt\"\n\n"
        "type Server struct {\n"
        "    Name string\n"
        "}\n\n"
        "func (s *Server) Start() error {\n"
        "    return nil\n"
        "}\n"
    )
    (root / "query.sql").write_text("SELECT * FROM users;\n")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="parsing-demo-") as tmp_dir:
        root = Path(tmp_dir)
        build_sample_repository(root)

        ingestion_result = IngestionService().ingest(str(root))
        parsing_result = ParsingService().parse_repository(str(root), ingestion_result)

        for parsed_file in sorted(parsing_result.parsed_files, key=lambda p: p.relative_path):
            print(f"{parsed_file.relative_path}  [{parsed_file.language}]")
            for imp in parsed_file.imports:
                alias_part = f" as {imp.alias}" if imp.alias else ""
                print(f"    import  {imp.module}{alias_part}  (line {imp.line})")
            for cls in parsed_file.classes:
                bases = f"({', '.join(cls.base_classes)})" if cls.base_classes else ""
                print(f"    class   {cls.name}{bases}  lines {cls.start_line}-{cls.end_line}")
            for func in parsed_file.functions:
                kind = "method" if func.is_method else "function"
                params = ", ".join(func.parameters)
                print(
                    f"    {kind:9s}{func.qualified_name}({params})  lines {func.start_line}-{func.end_line}"
                )
            if parsed_file.parse_errors:
                for error in parsed_file.parse_errors:
                    print(f"    ERROR: {error}")
            print()

        if parsing_result.skipped_files:
            print("Skipped (no entity extractor yet):")
            for path, reason in parsing_result.skipped_files:
                print(f"    {path}: {reason}")


if __name__ == "__main__":
    main()
