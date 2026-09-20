"""Per-language Tree-sitter grammar loading and symbol extraction.

Each `_extract_*` function takes a parsed Tree-sitter root node plus the
original source bytes and returns (imports, classes, functions) using
the shared models. The traversal *primitives* (node_text, descendant
search, line ranges) are identical across languages and live in
ts_utils.py; only node-type names and field names differ per grammar,
which is why each language still gets its own short function rather
than a single fully generic one.
"""

from functools import lru_cache
from typing import Callable, Optional

import tree_sitter_c as _tsc
import tree_sitter_cpp as _tscpp
import tree_sitter_go as _tsgo
import tree_sitter_java as _tsjava
import tree_sitter_javascript as _tsjs
import tree_sitter_typescript as _tsts
from tree_sitter import Language, Node

from parsing.models import ClassEntity, FunctionEntity, ImportEntity
from parsing.ts_utils import (
    all_descendants_of_type,
    direct_children_of_type,
    first_descendant_of_type,
    line_range,
    named_parameter_texts,
    node_text,
    strip_quotes,
)

Extractor = Callable[[Node, bytes], tuple[list[ImportEntity], list[ClassEntity], list[FunctionEntity]]]


@lru_cache(maxsize=None)
def load_grammar(language: str) -> Language:
    if language in ("JavaScript", "JSX"):
        return Language(_tsjs.language())
    if language == "TypeScript":
        return Language(_tsts.language_typescript())
    if language == "TSX":
        return Language(_tsts.language_tsx())
    if language == "Java":
        return Language(_tsjava.language())
    if language == "Go":
        return Language(_tsgo.language())
    if language == "C":
        return Language(_tsc.language())
    if language == "C++":
        return Language(_tscpp.language())
    raise ValueError(f"No Tree-sitter grammar configured for language: {language}")


# ---------------------------------------------------------------------------
# JavaScript / JSX / TypeScript / TSX share one grammar family: TS's grammar
# is a superset of JS's for the node types we care about (classes, methods,
# functions, imports), so one extractor covers all four.
# ---------------------------------------------------------------------------


def _extract_js_family(root: Node, source: bytes):
    imports: list[ImportEntity] = []
    classes: list[ClassEntity] = []
    functions: list[FunctionEntity] = []

    for class_node in all_descendants_of_type(root, ("class_declaration",)):
        name = node_text(class_node.child_by_field_name("name"), source)
        start, end = line_range(class_node)

        heritage = first_descendant_of_type(class_node, ("class_heritage",))
        base_classes = tuple(
            node_text(n, source)
            for n in all_descendants_of_type(heritage, ("identifier", "nested_identifier"))
        ) if heritage else ()

        classes.append(ClassEntity(name=name, start_line=start, end_line=end, base_classes=base_classes))

        body = class_node.child_by_field_name("body")
        if body is None:
            continue
        for method_node in direct_children_of_type(body, ("method_definition",)):
            method_name = node_text(method_node.child_by_field_name("name"), source)
            m_start, m_end = line_range(method_node)
            functions.append(
                FunctionEntity(
                    name=method_name,
                    qualified_name=f"{name}.{method_name}",
                    start_line=m_start,
                    end_line=m_end,
                    parameters=named_parameter_texts(method_node.child_by_field_name("parameters"), source),
                    is_method=True,
                    parent_class=name,
                )
            )

    for func_node in all_descendants_of_type(root, ("function_declaration",)):
        name = node_text(func_node.child_by_field_name("name"), source)
        start, end = line_range(func_node)
        functions.append(
            FunctionEntity(
                name=name,
                qualified_name=name,
                start_line=start,
                end_line=end,
                parameters=named_parameter_texts(func_node.child_by_field_name("parameters"), source),
                is_method=False,
                parent_class=None,
            )
        )

    for import_node in all_descendants_of_type(root, ("import_statement",)):
        source_node = import_node.child_by_field_name("source")
        module = strip_quotes(node_text(source_node, source))
        line = line_range(import_node)[0]
        imports.append(ImportEntity(module=module, alias=None, line=line))

    return imports, classes, functions


# ---------------------------------------------------------------------------
# Java: classes/interfaces, methods/constructors (always inside a class),
# and import declarations.
# ---------------------------------------------------------------------------


def _extract_java(root: Node, source: bytes):
    imports: list[ImportEntity] = []
    classes: list[ClassEntity] = []
    functions: list[FunctionEntity] = []

    for class_node in all_descendants_of_type(root, ("class_declaration", "interface_declaration")):
        name = node_text(class_node.child_by_field_name("name"), source)
        start, end = line_range(class_node)

        base_classes: list[str] = []
        superclass = class_node.child_by_field_name("superclass")
        if superclass is not None:
            base_classes.extend(node_text(n, source) for n in all_descendants_of_type(superclass, ("type_identifier",)))
        interfaces = class_node.child_by_field_name("interfaces")
        if interfaces is not None:
            base_classes.extend(node_text(n, source) for n in all_descendants_of_type(interfaces, ("type_identifier",)))

        classes.append(ClassEntity(name=name, start_line=start, end_line=end, base_classes=tuple(base_classes)))

        body = class_node.child_by_field_name("body")
        if body is None:
            continue
        for method_node in direct_children_of_type(body, ("method_declaration", "constructor_declaration")):
            method_name = node_text(method_node.child_by_field_name("name"), source)
            m_start, m_end = line_range(method_node)
            functions.append(
                FunctionEntity(
                    name=method_name,
                    qualified_name=f"{name}.{method_name}",
                    start_line=m_start,
                    end_line=m_end,
                    parameters=named_parameter_texts(method_node.child_by_field_name("parameters"), source),
                    is_method=True,
                    parent_class=name,
                )
            )

    for import_node in all_descendants_of_type(root, ("import_declaration",)):
        text = node_text(import_node, source)
        module = text.removeprefix("import").removesuffix(";").replace("static", "", 1).strip()
        line = line_range(import_node)[0]
        imports.append(ImportEntity(module=module, alias=None, line=line))

    return imports, classes, functions


# ---------------------------------------------------------------------------
# Go: functions, methods (receiver identifies the "owning type"), and
# struct/interface type declarations treated as class-equivalents.
# ---------------------------------------------------------------------------


def _go_receiver_type_name(receiver: Node, source: bytes) -> str:
    param = receiver.named_children[0] if receiver.named_children else None
    if param is None:
        return ""
    type_node = param.child_by_field_name("type")
    if type_node is None:
        return ""
    if type_node.type == "pointer_type" and type_node.named_children:
        return node_text(type_node.named_children[0], source)
    return node_text(type_node, source)


def _extract_go(root: Node, source: bytes):
    imports: list[ImportEntity] = []
    classes: list[ClassEntity] = []
    functions: list[FunctionEntity] = []

    for type_spec in all_descendants_of_type(root, ("type_spec",)):
        if not any(child.type in ("struct_type", "interface_type") for child in type_spec.children):
            continue
        name = node_text(type_spec.child_by_field_name("name"), source)
        start, end = line_range(type_spec)
        classes.append(ClassEntity(name=name, start_line=start, end_line=end, base_classes=()))

    for func_node in all_descendants_of_type(root, ("function_declaration",)):
        name = node_text(func_node.child_by_field_name("name"), source)
        start, end = line_range(func_node)
        functions.append(
            FunctionEntity(
                name=name,
                qualified_name=name,
                start_line=start,
                end_line=end,
                parameters=named_parameter_texts(func_node.child_by_field_name("parameters"), source),
                is_method=False,
                parent_class=None,
            )
        )

    for method_node in all_descendants_of_type(root, ("method_declaration",)):
        name = node_text(method_node.child_by_field_name("name"), source)
        start, end = line_range(method_node)
        receiver = method_node.child_by_field_name("receiver")
        parent = _go_receiver_type_name(receiver, source) if receiver is not None else ""
        functions.append(
            FunctionEntity(
                name=name,
                qualified_name=f"{parent}.{name}" if parent else name,
                start_line=start,
                end_line=end,
                parameters=named_parameter_texts(method_node.child_by_field_name("parameters"), source),
                is_method=True,
                parent_class=parent or None,
            )
        )

    for import_spec in all_descendants_of_type(root, ("import_spec",)):
        path_node = import_spec.child_by_field_name("path")
        module = strip_quotes(node_text(path_node, source))
        alias_node = import_spec.child_by_field_name("name")
        alias = node_text(alias_node, source) if alias_node is not None else None
        line = line_range(import_spec)[0]
        imports.append(ImportEntity(module=module, alias=alias, line=line))

    return imports, classes, functions


# ---------------------------------------------------------------------------
# C / C++: no stdlib-style parser exists, so we lean on Tree-sitter's
# declarator structure. Function *names* require unwrapping pointer/
# reference declarators and (in C++) qualified/scoped names; this is the
# most heuristic part of Phase 2 and its limits are documented in
# treesitter_parser.py's module docstring.
# ---------------------------------------------------------------------------


def _function_name_and_parent(func_node: Node, source: bytes) -> tuple[str, Optional[str]]:
    declarator = func_node.child_by_field_name("declarator")
    if declarator is None:
        return "", None

    # Unwrap pointer/reference return-type declarators to reach the
    # function_declarator (e.g. `int *make_ptr(...)`).
    while declarator.type in ("pointer_declarator", "reference_declarator"):
        inner = declarator.child_by_field_name("declarator")
        if inner is None:
            break
        declarator = inner

    if declarator.type != "function_declarator":
        return node_text(declarator, source), None

    name_node = declarator.child_by_field_name("declarator")
    if name_node is None:
        return "", None

    if name_node.type == "qualified_identifier":
        scope = node_text(name_node.child_by_field_name("scope"), source)
        name = node_text(name_node.child_by_field_name("name"), source)
        return name, scope or None

    return node_text(name_node, source), None


def _extract_c_like(root: Node, source: bytes, *, with_classes: bool):
    imports: list[ImportEntity] = []
    classes: list[ClassEntity] = []
    functions: list[FunctionEntity] = []

    if with_classes:
        for class_node in all_descendants_of_type(root, ("class_specifier", "struct_specifier")):
            name_node = class_node.child_by_field_name("name")
            if name_node is None:
                continue  # anonymous struct/class, nothing to name
            name = node_text(name_node, source)
            start, end = line_range(class_node)

            base_clause = first_descendant_of_type(class_node, ("base_class_clause",))
            base_classes = tuple(
                node_text(n, source)
                for n in all_descendants_of_type(base_clause, ("type_identifier", "qualified_identifier"))
            ) if base_clause else ()

            classes.append(ClassEntity(name=name, start_line=start, end_line=end, base_classes=base_classes))

    known_class_names = {c.name for c in classes}

    for func_node in all_descendants_of_type(root, ("function_definition",)):
        name, scope = _function_name_and_parent(func_node, source)
        if not name:
            continue
        start, end = line_range(func_node)

        parent_class = scope
        if parent_class is None:
            enclosing = func_node.parent
            while enclosing is not None:
                if enclosing.type in ("class_specifier", "struct_specifier"):
                    name_node = enclosing.child_by_field_name("name")
                    parent_class = node_text(name_node, source) if name_node else None
                    break
                enclosing = enclosing.parent

        declarator = func_node.child_by_field_name("declarator")
        params_node = None
        unwrapped = declarator
        while unwrapped is not None and unwrapped.type != "function_declarator":
            unwrapped = unwrapped.child_by_field_name("declarator")
        if unwrapped is not None:
            params_node = unwrapped.child_by_field_name("parameters")

        functions.append(
            FunctionEntity(
                name=name,
                qualified_name=f"{parent_class}.{name}" if parent_class else name,
                start_line=start,
                end_line=end,
                parameters=named_parameter_texts(params_node, source),
                is_method=parent_class is not None,
                parent_class=parent_class,
            )
        )

    for include_node in all_descendants_of_type(root, ("preproc_include",)):
        path_node = include_node.child_by_field_name("path")
        module = strip_quotes(node_text(path_node, source))
        line = line_range(include_node)[0]
        imports.append(ImportEntity(module=module, alias=None, line=line))

    return imports, classes, functions


def _extract_c(root: Node, source: bytes):
    return _extract_c_like(root, source, with_classes=False)


def _extract_cpp(root: Node, source: bytes):
    return _extract_c_like(root, source, with_classes=True)


EXTRACTORS: dict[str, Extractor] = {
    "JavaScript": _extract_js_family,
    "JSX": _extract_js_family,
    "TypeScript": _extract_js_family,
    "TSX": _extract_js_family,
    "Java": _extract_java,
    "Go": _extract_go,
    "C": _extract_c,
    "C++": _extract_cpp,
}
