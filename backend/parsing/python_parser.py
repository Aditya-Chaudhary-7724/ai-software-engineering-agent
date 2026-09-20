"""Python symbol extraction using the standard library `ast` module.

No external dependency is justified here: `ast` is exact (it is the
same parser CPython itself uses) and already provides `end_lineno`.

Limitations (documented rather than worked around in Phase 2):
- `qualified_name` reflects only the immediate enclosing class, not a
  full nesting chain (a method of a nested class `Outer.Inner.method`
  is reported as `Inner.method`).
- Functions nested inside other functions (closures) are extracted as
  top-level-looking entities with `is_method=False`; their enclosing
  function is not tracked.
"""

import ast
from typing import Optional

from parsing.models import ClassEntity, FunctionEntity, ImportEntity, ParsedFile


def _dotted_name(node: ast.AST) -> str:
    """Render a Name/Attribute chain (e.g. a base class) as a dotted string."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr}"
    return ast.dump(node)


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: list[ImportEntity] = []
        self.classes: list[ClassEntity] = []
        self.functions: list[FunctionEntity] = []
        self._class_stack: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(ImportEntity(module=alias.name, alias=alias.asname, line=node.lineno))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            full_module = f"{module}.{alias.name}" if module else alias.name
            self.imports.append(
                ImportEntity(module=full_module, alias=alias.asname, line=node.lineno)
            )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        base_classes = tuple(_dotted_name(base) for base in node.bases)
        self.classes.append(
            ClassEntity(
                name=node.name,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                base_classes=base_classes,
            )
        )
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def _visit_function(self, node) -> None:
        parent_class: Optional[str] = self._class_stack[-1] if self._class_stack else None
        qualified_name = f"{parent_class}.{node.name}" if parent_class else node.name
        parameters = tuple(arg.arg for arg in node.args.args)

        self.functions.append(
            FunctionEntity(
                name=node.name,
                qualified_name=qualified_name,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                parameters=parameters,
                is_method=parent_class is not None,
                parent_class=parent_class,
            )
        )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


def parse_python_file(source: str, relative_path: str) -> ParsedFile:
    """Parse Python source into a ParsedFile. Syntax errors are recorded, not raised."""
    parsed = ParsedFile(relative_path=relative_path, language="Python")

    try:
        tree = ast.parse(source, filename=relative_path)
    except (SyntaxError, ValueError) as exc:
        parsed.parse_errors.append(f"Syntax error: {exc}")
        return parsed

    visitor = _SymbolVisitor()
    visitor.visit(tree)

    parsed.imports = visitor.imports
    parsed.classes = visitor.classes
    parsed.functions = visitor.functions
    return parsed
