from parsing.treesitter_parser import parse_with_treesitter

GO_SAMPLE = b'''
package main

import (
    "fmt"
    myfmt "fmt"
)

type Server struct {
    Name string
}

func (s *Server) Start() error {
    return nil
}

func NewServer() *Server {
    return &Server{}
}
'''


def test_go_extracts_grouped_imports_with_alias():
    result = parse_with_treesitter(GO_SAMPLE, "server.go", "Go")
    assert result.parse_errors == []
    modules = [(i.module, i.alias) for i in result.imports]
    assert ("fmt", None) in modules
    assert ("fmt", "myfmt") in modules


def test_go_extracts_struct_as_class_entity():
    result = parse_with_treesitter(GO_SAMPLE, "server.go", "Go")
    assert result.classes[0].name == "Server"


def test_go_extracts_method_with_pointer_receiver_and_function():
    result = parse_with_treesitter(GO_SAMPLE, "server.go", "Go")
    by_qualified_name = {f.qualified_name: f for f in result.functions}
    assert by_qualified_name["Server.Start"].is_method is True
    assert by_qualified_name["Server.Start"].parent_class == "Server"
    assert by_qualified_name["NewServer"].is_method is False
