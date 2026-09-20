from parsing.treesitter_parser import parse_with_treesitter

JAVA_SAMPLE = b'''
import java.util.List;

public class Animal implements Comparable {
    public Animal() {}

    public String speak() {
        return "...";
    }
}
'''


def test_java_extracts_import():
    result = parse_with_treesitter(JAVA_SAMPLE, "Animal.java", "Java")
    assert result.parse_errors == []
    assert result.imports[0].module == "java.util.List"


def test_java_extracts_class_with_interface():
    result = parse_with_treesitter(JAVA_SAMPLE, "Animal.java", "Java")
    assert result.classes[0].name == "Animal"
    assert "Comparable" in result.classes[0].base_classes


def test_java_extracts_constructor_and_method_as_methods():
    result = parse_with_treesitter(JAVA_SAMPLE, "Animal.java", "Java")
    names = {f.qualified_name for f in result.functions}
    assert names == {"Animal.Animal", "Animal.speak"}
    assert all(f.is_method for f in result.functions)
