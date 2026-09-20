from parsing.python_parser import parse_python_file

SAMPLE = '''
import os
from typing import Optional as Opt


class Animal:
    def __init__(self, name):
        self.name = name

    def speak(self) -> str:
        return "..."


class Dog(Animal):
    def speak(self) -> str:
        return "Woof"


def top_level(x, y):
    return x + y
'''


def test_parse_python_extracts_imports():
    result = parse_python_file(SAMPLE, "animals.py")

    modules = {(i.module, i.alias) for i in result.imports}
    assert ("os", None) in modules
    assert ("typing.Optional", "Opt") in modules


def test_parse_python_extracts_classes_with_line_ranges():
    result = parse_python_file(SAMPLE, "animals.py")

    by_name = {c.name: c for c in result.classes}
    assert set(by_name) == {"Animal", "Dog"}
    assert by_name["Dog"].base_classes == ("Animal",)
    assert by_name["Animal"].start_line == 6
    assert by_name["Animal"].end_line == 11


def test_parse_python_extracts_methods_and_functions():
    result = parse_python_file(SAMPLE, "animals.py")

    by_qualified_name = {f.qualified_name: f for f in result.functions}
    assert by_qualified_name["Animal.speak"].is_method is True
    assert by_qualified_name["Animal.speak"].parent_class == "Animal"
    assert by_qualified_name["Dog.speak"].parent_class == "Dog"
    assert by_qualified_name["top_level"].is_method is False
    assert by_qualified_name["top_level"].parameters == ("x", "y")


def test_parse_python_syntax_error_is_recorded_not_raised():
    result = parse_python_file("def broken(:\n    pass\n", "broken.py")

    assert result.classes == []
    assert result.functions == []
    assert len(result.parse_errors) == 1
    assert "Syntax error" in result.parse_errors[0]


def test_parse_python_async_function_extracted():
    result = parse_python_file("async def fetch():\n    pass\n", "a.py")

    assert result.functions[0].name == "fetch"
    assert result.functions[0].is_method is False
