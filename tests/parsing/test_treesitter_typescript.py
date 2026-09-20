from parsing.treesitter_parser import parse_with_treesitter

TS_SAMPLE = b'''
import { Injectable } from "@angular/core";

class Repository {
    save(item: string): void {
    }
}

function identity<T>(value: T): T {
    return value;
}
'''

TSX_SAMPLE = b'''
function Button(props: { label: string }) {
    return <button>{props.label}</button>;
}
'''


def test_typescript_extracts_class_method_and_function():
    result = parse_with_treesitter(TS_SAMPLE, "repo.ts", "TypeScript")

    assert result.parse_errors == []
    assert result.classes[0].name == "Repository"
    by_qualified_name = {f.qualified_name: f for f in result.functions}
    assert by_qualified_name["Repository.save"].is_method is True
    assert by_qualified_name["identity"].is_method is False
    assert result.imports[0].module == "@angular/core"


def test_tsx_extracts_function_component():
    result = parse_with_treesitter(TSX_SAMPLE, "Button.tsx", "TSX")

    assert result.parse_errors == []
    assert result.functions[0].name == "Button"
