from parsing.treesitter_parser import parse_with_treesitter

C_SAMPLE = b'''
#include <stdio.h>
#include "myheader.h"

int add(int a, int b) {
    return a + b;
}

int *make_ptr(int x) {
    return 0;
}
'''


def test_c_extracts_system_and_local_includes():
    result = parse_with_treesitter(C_SAMPLE, "main.c", "C")
    assert result.parse_errors == []
    modules = {i.module for i in result.imports}
    assert modules == {"stdio.h", "myheader.h"}


def test_c_extracts_plain_and_pointer_returning_functions():
    result = parse_with_treesitter(C_SAMPLE, "main.c", "C")
    by_name = {f.name: f for f in result.functions}
    assert by_name["add"].parameters == ("int a", "int b")
    assert by_name["add"].is_method is False
    assert "make_ptr" in by_name


def test_c_has_no_classes():
    result = parse_with_treesitter(C_SAMPLE, "main.c", "C")
    assert result.classes == []
