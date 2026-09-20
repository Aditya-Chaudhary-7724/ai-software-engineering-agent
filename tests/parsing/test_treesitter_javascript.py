from parsing.treesitter_parser import parse_with_treesitter

JS_SAMPLE = b'''
import { readFile } from "fs";
import React from "react";

class Animal {
    speak() {
        return "...";
    }
}

class Dog extends Animal {
    speak() {
        return "Woof";
    }
}

function add(a, b) {
    return a + b;
}
'''


def test_js_extracts_imports():
    result = parse_with_treesitter(JS_SAMPLE, "app.js", "JavaScript")

    modules = {i.module for i in result.imports}
    assert modules == {"fs", "react"}


def test_js_extracts_classes_and_inheritance():
    result = parse_with_treesitter(JS_SAMPLE, "app.js", "JavaScript")

    by_name = {c.name: c for c in result.classes}
    assert set(by_name) == {"Animal", "Dog"}
    assert by_name["Dog"].base_classes == ("Animal",)


def test_js_extracts_methods_and_top_level_function():
    result = parse_with_treesitter(JS_SAMPLE, "app.js", "JavaScript")

    by_qualified_name = {f.qualified_name: f for f in result.functions}
    assert by_qualified_name["Animal.speak"].is_method is True
    assert by_qualified_name["Dog.speak"].parent_class == "Dog"
    assert by_qualified_name["add"].is_method is False
    assert by_qualified_name["add"].parameters == ("a", "b")


def test_jsx_uses_javascript_grammar_and_extracts_function():
    source = b'''
function Greeting() {
    return <div>Hello</div>;
}
'''
    result = parse_with_treesitter(source, "Greeting.jsx", "JSX")
    assert result.parse_errors == []
    assert result.functions[0].name == "Greeting"


def test_no_extractor_configured_records_error_not_raises():
    result = parse_with_treesitter(b"whatever", "file.rs", "Rust")

    assert result.parse_errors == ["No Tree-sitter extractor configured for language: Rust"]
