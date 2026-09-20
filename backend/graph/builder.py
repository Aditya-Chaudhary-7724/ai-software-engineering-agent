"""Builds the knowledge graph for one repository from Phase 1 + Phase 2 output.

Only relationships that can actually be derived from what's already
extracted are created:

- CONTAINS: Repository -> Folder -> ... -> File, from relative_path
  structure alone.
- DEFINES: File -> Class, File -> top-level Function, Class -> method
  Function.
- IMPORTS: File -> Module (always, from the raw import string), plus
  Module -RESOLVES_TO-> File when the import can be matched to a real
  file in the same repository (see resolution.py for what's attempted
  per language and what isn't).
- INHERITS: Class -> Class, only when a base-class name matches
  exactly one class in the whole repository (see resolution.py). If
  it's ambiguous or unresolved, the raw base-class names are still
  kept as a `base_class_names` property on the Class node — nothing is
  silently dropped, it's just not turned into a graph edge without
  confidence.

Deliberately NOT created: CALLS, USES, DEPENDS_ON. None of these can
be derived from Phase 2's output today (it extracts definitions —
functions, classes, imports — not call-sites or expression usage
inside function bodies). Inventing these edges from name-matching
alone would risk exactly the "hallucinated CALLS relationships" this
phase was explicitly told not to produce.
"""

from pathlib import Path

from ingestion.models import IngestionResult
from parsing.models import ParsingResult

from graph.client import Neo4jClient
from graph.models import GraphBuildResult
from graph.resolution import build_class_index, resolve_import_to_file


class GraphBuilder:
    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    def build(
        self, repository_root: str, ingestion_result: IngestionResult, parsing_result: ParsingResult
    ) -> GraphBuildResult:
        root_path = str(Path(repository_root).resolve())
        result = GraphBuildResult(repository_root_path=root_path)

        self._client.run(
            "MERGE (r:Repository {root_path: $root_path}) SET r.name = $name",
            root_path=root_path,
            name=ingestion_result.repository.name,
        )
        result.node_counts["Repository"] = 1

        self._build_folders_and_files(root_path, ingestion_result, result)
        self._build_classes_and_functions(root_path, parsing_result, result)
        self._build_imports(root_path, ingestion_result, parsing_result, result)
        self._build_inheritance(root_path, parsing_result, result)

        return result

    def _build_folders_and_files(
        self, root_path: str, ingestion_result: IngestionResult, result: GraphBuildResult
    ) -> None:
        folder_parents: dict[str, str] = {}  # folder_path -> parent_folder_path
        root_folders: set[str] = set()  # folder_paths whose parent is the Repository itself

        for file_meta in ingestion_result.files:
            parts = file_meta.relative_path.split("/")[:-1]
            accumulated = ""
            for part in parts:
                previous = accumulated
                accumulated = f"{accumulated}/{part}" if accumulated else part
                if accumulated in folder_parents or accumulated in root_folders:
                    continue
                if previous:
                    folder_parents[accumulated] = previous
                else:
                    root_folders.add(accumulated)

        if root_folders:
            self._client.run(
                """
                UNWIND $folders AS folder_path
                MERGE (f:Folder {repository_root_path: $root_path, relative_path: folder_path})
                WITH f
                MATCH (r:Repository {root_path: $root_path})
                MERGE (r)-[:CONTAINS]->(f)
                """,
                root_path=root_path,
                folders=sorted(root_folders),
            )
        if folder_parents:
            self._client.run(
                """
                UNWIND $rows AS row
                MERGE (child:Folder {repository_root_path: $root_path, relative_path: row.folder})
                MERGE (parent:Folder {repository_root_path: $root_path, relative_path: row.parent})
                MERGE (parent)-[:CONTAINS]->(child)
                """,
                root_path=root_path,
                rows=[{"folder": f, "parent": p} for f, p in folder_parents.items()],
            )
        result.node_counts["Folder"] = len(root_folders) + len(folder_parents)
        result.relationship_counts["CONTAINS"] = (
            result.relationship_counts.get("CONTAINS", 0) + len(root_folders) + len(folder_parents)
        )

        files_at_root = []
        files_in_folder = []
        for file_meta in ingestion_result.files:
            parts = file_meta.relative_path.split("/")
            row = {
                "relative_path": file_meta.relative_path,
                "filename": file_meta.filename,
                "language": file_meta.language,
                "category": file_meta.category,
                "size_bytes": file_meta.size_bytes,
            }
            if len(parts) > 1:
                row["folder"] = "/".join(parts[:-1])
                files_in_folder.append(row)
            else:
                files_at_root.append(row)

        if files_at_root:
            self._client.run(
                """
                UNWIND $rows AS row
                MERGE (f:File {repository_root_path: $root_path, relative_path: row.relative_path})
                SET f.filename = row.filename, f.language = row.language,
                    f.category = row.category, f.size_bytes = row.size_bytes
                WITH f
                MATCH (r:Repository {root_path: $root_path})
                MERGE (r)-[:CONTAINS]->(f)
                """,
                root_path=root_path,
                rows=files_at_root,
            )
        if files_in_folder:
            self._client.run(
                """
                UNWIND $rows AS row
                MERGE (f:File {repository_root_path: $root_path, relative_path: row.relative_path})
                SET f.filename = row.filename, f.language = row.language,
                    f.category = row.category, f.size_bytes = row.size_bytes
                WITH f, row
                MATCH (parent:Folder {repository_root_path: $root_path, relative_path: row.folder})
                MERGE (parent)-[:CONTAINS]->(f)
                """,
                root_path=root_path,
                rows=files_in_folder,
            )
        file_count = len(files_at_root) + len(files_in_folder)
        result.node_counts["File"] = file_count
        result.relationship_counts["CONTAINS"] += file_count

    def _build_classes_and_functions(
        self, root_path: str, parsing_result: ParsingResult, result: GraphBuildResult
    ) -> None:
        class_rows = []
        top_level_function_rows = []
        method_rows = []

        for parsed_file in parsing_result.parsed_files:
            for cls in parsed_file.classes:
                class_rows.append(
                    {
                        "relative_path": parsed_file.relative_path,
                        "name": cls.name,
                        "start_line": cls.start_line,
                        "end_line": cls.end_line,
                        "base_class_names": list(cls.base_classes),
                    }
                )
            for func in parsed_file.functions:
                row = {
                    "relative_path": parsed_file.relative_path,
                    "name": func.name,
                    "qualified_name": func.qualified_name,
                    "start_line": func.start_line,
                    "end_line": func.end_line,
                    "parameters": list(func.parameters),
                }
                if func.is_method and func.parent_class:
                    row["parent_class"] = func.parent_class
                    method_rows.append(row)
                else:
                    top_level_function_rows.append(row)

        if class_rows:
            self._client.run(
                """
                UNWIND $rows AS row
                MERGE (c:Class {repository_root_path: $root_path, relative_path: row.relative_path, name: row.name})
                SET c.start_line = row.start_line, c.end_line = row.end_line,
                    c.base_class_names = row.base_class_names
                WITH c, row
                MATCH (f:File {repository_root_path: $root_path, relative_path: row.relative_path})
                MERGE (f)-[:DEFINES]->(c)
                """,
                root_path=root_path,
                rows=class_rows,
            )
        if top_level_function_rows:
            self._client.run(
                """
                UNWIND $rows AS row
                MERGE (fn:Function {repository_root_path: $root_path, relative_path: row.relative_path,
                                     qualified_name: row.qualified_name})
                SET fn.name = row.name, fn.start_line = row.start_line, fn.end_line = row.end_line,
                    fn.parameters = row.parameters
                WITH fn, row
                MATCH (f:File {repository_root_path: $root_path, relative_path: row.relative_path})
                MERGE (f)-[:DEFINES]->(fn)
                """,
                root_path=root_path,
                rows=top_level_function_rows,
            )
        if method_rows:
            self._client.run(
                """
                UNWIND $rows AS row
                MERGE (fn:Function {repository_root_path: $root_path, relative_path: row.relative_path,
                                     qualified_name: row.qualified_name})
                SET fn.name = row.name, fn.start_line = row.start_line, fn.end_line = row.end_line,
                    fn.parameters = row.parameters
                WITH fn, row
                MATCH (c:Class {repository_root_path: $root_path, relative_path: row.relative_path,
                                 name: row.parent_class})
                MERGE (c)-[:DEFINES]->(fn)
                """,
                root_path=root_path,
                rows=method_rows,
            )

        result.node_counts["Class"] = len(class_rows)
        result.node_counts["Function"] = len(top_level_function_rows) + len(method_rows)
        result.relationship_counts["DEFINES"] = len(class_rows) + len(top_level_function_rows) + len(method_rows)

    def _build_imports(
        self,
        root_path: str,
        ingestion_result: IngestionResult,
        parsing_result: ParsingResult,
        result: GraphBuildResult,
    ) -> None:
        known_files = {f.relative_path for f in ingestion_result.files}
        import_rows = []
        resolved_rows = []
        module_names: set[str] = set()

        for parsed_file in parsing_result.parsed_files:
            for imp in parsed_file.imports:
                module_names.add(imp.module)
                import_rows.append({"relative_path": parsed_file.relative_path, "module": imp.module})

                resolved_path = resolve_import_to_file(
                    parsed_file.language, imp.module, parsed_file.relative_path, known_files
                )
                if resolved_path:
                    resolved_rows.append({"module": imp.module, "relative_path": resolved_path})

        if import_rows:
            self._client.run(
                """
                UNWIND $rows AS row
                MERGE (m:Module {repository_root_path: $root_path, name: row.module})
                WITH m, row
                MATCH (f:File {repository_root_path: $root_path, relative_path: row.relative_path})
                MERGE (f)-[:IMPORTS]->(m)
                """,
                root_path=root_path,
                rows=import_rows,
            )
        if resolved_rows:
            self._client.run(
                """
                UNWIND $rows AS row
                MATCH (m:Module {repository_root_path: $root_path, name: row.module})
                MATCH (f:File {repository_root_path: $root_path, relative_path: row.relative_path})
                MERGE (m)-[:RESOLVES_TO]->(f)
                """,
                root_path=root_path,
                rows=resolved_rows,
            )

        result.node_counts["Module"] = len(module_names)
        result.relationship_counts["IMPORTS"] = len(import_rows)
        result.relationship_counts["RESOLVES_TO"] = len(resolved_rows)

    def _build_inheritance(
        self, root_path: str, parsing_result: ParsingResult, result: GraphBuildResult
    ) -> None:
        class_index = build_class_index(parsing_result)
        inherits_rows = []

        for parsed_file in parsing_result.parsed_files:
            for cls in parsed_file.classes:
                for base_name in cls.base_classes:
                    matches = class_index.get(base_name, [])
                    if len(matches) == 1:
                        inherits_rows.append(
                            {
                                "child_path": parsed_file.relative_path,
                                "child_name": cls.name,
                                "parent_path": matches[0],
                                "parent_name": base_name,
                            }
                        )

        if inherits_rows:
            self._client.run(
                """
                UNWIND $rows AS row
                MATCH (child:Class {repository_root_path: $root_path, relative_path: row.child_path, name: row.child_name})
                MATCH (parent:Class {repository_root_path: $root_path, relative_path: row.parent_path, name: row.parent_name})
                MERGE (child)-[:INHERITS]->(parent)
                """,
                root_path=root_path,
                rows=inherits_rows,
            )

        result.relationship_counts["INHERITS"] = len(inherits_rows)
