"""Low-level Tree-sitter node helpers shared by all per-language extractors."""

from typing import Iterable, Iterator, Optional

from tree_sitter import Node


def node_text(node: Optional[Node], source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def strip_quotes(text: str) -> str:
    return text.strip("\"'<>")


def line_range(node: Node) -> tuple[int, int]:
    """Tree-sitter rows are 0-indexed; ParsedFile line numbers are 1-indexed."""
    return node.start_point[0] + 1, node.end_point[0] + 1


def direct_children_of_type(node: Node, types: Iterable[str]) -> list[Node]:
    type_set = set(types)
    return [child for child in node.children if child.type in type_set]


def all_descendants_of_type(
    node: Node, types: Iterable[str], stop_types: Iterable[str] = ()
) -> Iterator[Node]:
    """Depth-first search for descendants of the given types.

    Does not descend past a node whose type is in `stop_types` — used to
    avoid e.g. matching a nested class's methods as the outer class's.
    """
    type_set = set(types)
    stop_set = set(stop_types)
    for child in node.children:
        if child.type in type_set:
            yield child
        if child.type not in stop_set:
            yield from all_descendants_of_type(child, type_set, stop_set)


def first_descendant_of_type(node: Node, types: Iterable[str]) -> Optional[Node]:
    for match in all_descendants_of_type(node, types):
        return match
    return None


def named_parameter_texts(parameters_node: Optional[Node], source: bytes) -> tuple[str, ...]:
    if parameters_node is None:
        return ()
    return tuple(node_text(child, source) for child in parameters_node.named_children)
