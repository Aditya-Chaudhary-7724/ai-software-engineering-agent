from parsing.models import ClassEntity, ParsedFile

from graph.resolution import build_class_index, resolve_import_to_file


def test_resolve_python_import_style():
    known_files = {"pkg/module.py"}
    assert resolve_import_to_file("Python", "pkg.module", "main.py", known_files) == "pkg/module.py"


def test_resolve_python_from_import_style():
    """`from pkg import module` and `import pkg.module` are recorded
    identically by Phase 2 as module="pkg.module" — both must resolve.
    """
    known_files = {"pkg.py"}
    assert resolve_import_to_file("Python", "pkg.thing", "main.py", known_files) == "pkg.py"


def test_resolve_python_package_init():
    known_files = {"pkg/__init__.py"}
    assert resolve_import_to_file("Python", "pkg", "main.py", known_files) == "pkg/__init__.py"


def test_resolve_python_unresolvable_returns_none():
    known_files = {"other.py"}
    assert resolve_import_to_file("Python", "nonexistent", "main.py", known_files) is None


def test_resolve_js_relative_import():
    known_files = {"src/utils.js"}
    result = resolve_import_to_file("JavaScript", "./utils", "src/main.js", known_files)
    assert result == "src/utils.js"


def test_resolve_js_relative_import_parent_dir():
    known_files = {"lib/helper.ts"}
    result = resolve_import_to_file("TypeScript", "../lib/helper", "src/main.ts", known_files)
    assert result == "lib/helper.ts"


def test_resolve_js_bare_specifier_is_not_attempted():
    known_files = {"react.js"}
    assert resolve_import_to_file("JavaScript", "react", "src/main.js", known_files) is None


def test_resolve_js_index_file():
    known_files = {"src/components/index.tsx"}
    result = resolve_import_to_file("TSX", "./components", "src/main.tsx", known_files)
    assert result == "src/components/index.tsx"


def test_resolve_java_import():
    known_files = {"com/example/Foo.java"}
    result = resolve_import_to_file("Java", "com.example.Foo", "com/example/Main.java", known_files)
    assert result == "com/example/Foo.java"


def test_resolve_java_wildcard_not_attempted():
    known_files = {"com/example/Foo.java"}
    assert resolve_import_to_file("Java", "com.example.*", "Main.java", set(known_files)) is None


def test_resolve_go_not_attempted():
    assert resolve_import_to_file("Go", "myproject/pkg/util", "main.go", {"pkg/util.go"}) is None


def test_resolve_c_not_attempted():
    assert resolve_import_to_file("C", "myheader.h", "main.c", {"myheader.h"}) is None


def _parsed_file(relative_path, class_names):
    return ParsedFile(
        relative_path=relative_path,
        language="Python",
        classes=[ClassEntity(name=n, start_line=1, end_line=2, base_classes=()) for n in class_names],
    )


def test_build_class_index_maps_names_to_files():
    parsing_result_files = [_parsed_file("a.py", ["Animal"]), _parsed_file("b.py", ["Dog", "Cat"])]

    from parsing.models import ParsingResult

    result = ParsingResult(parsed_files=parsing_result_files)
    index = build_class_index(result)

    assert index["Animal"] == ["a.py"]
    assert index["Dog"] == ["b.py"]
    assert index["Cat"] == ["b.py"]


def test_build_class_index_tracks_duplicate_names_across_files():
    from parsing.models import ParsingResult

    result = ParsingResult(parsed_files=[_parsed_file("a.py", ["Handler"]), _parsed_file("b.py", ["Handler"])])
    index = build_class_index(result)

    assert sorted(index["Handler"]) == ["a.py", "b.py"]
