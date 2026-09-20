from parsing.treesitter_parser import parse_with_treesitter

CPP_SAMPLE = b'''
#include <string>

class Animal {
public:
    Animal();
    void speak() {
    }
};

void Animal::run() {
}

int add(int a, int b) {
    return a + b;
}
'''


def test_cpp_extracts_class():
    result = parse_with_treesitter(CPP_SAMPLE, "animal.cpp", "C++")
    assert result.parse_errors == []
    assert result.classes[0].name == "Animal"


def test_cpp_extracts_inline_method():
    result = parse_with_treesitter(CPP_SAMPLE, "animal.cpp", "C++")
    by_qualified_name = {f.qualified_name: f for f in result.functions}
    assert by_qualified_name["Animal.speak"].is_method is True
    assert by_qualified_name["Animal.speak"].parent_class == "Animal"


def test_cpp_extracts_out_of_class_qualified_method():
    result = parse_with_treesitter(CPP_SAMPLE, "animal.cpp", "C++")
    by_qualified_name = {f.qualified_name: f for f in result.functions}
    assert by_qualified_name["Animal.run"].is_method is True
    assert by_qualified_name["Animal.run"].parent_class == "Animal"


def test_cpp_extracts_free_function():
    result = parse_with_treesitter(CPP_SAMPLE, "animal.cpp", "C++")
    by_qualified_name = {f.qualified_name: f for f in result.functions}
    assert by_qualified_name["add"].is_method is False
