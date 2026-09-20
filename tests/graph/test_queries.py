"""LOCAL TESTED against the real local Neo4j container; skips cleanly
if unreachable (see conftest.py).
"""

from ingestion.service import IngestionService
from parsing.service import ParsingService

from graph.builder import GraphBuilder
from graph.queries import (
    find_symbol,
    get_class_ancestors,
    get_file_dependencies,
    get_importers_of_file,
    get_repository_summary,
)
from tests.graph.conftest import requires_neo4j


def _build(tmp_path, client):
    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    GraphBuilder(client).build(str(tmp_path), ingestion_result, parsing_result)
    return str(tmp_path.resolve())


@requires_neo4j
def test_get_repository_summary_reflects_real_counts(tmp_path, neo4j_client):
    (tmp_path / "main.py").write_text("class Foo:\n    def bar(self):\n        pass\n")
    root_path = _build(tmp_path, neo4j_client)

    summary = get_repository_summary(neo4j_client, root_path)

    assert summary["files"] == 1
    assert summary["classes"] == 1
    assert summary["functions"] == 1


@requires_neo4j
def test_get_file_dependencies_shows_resolved_and_external(tmp_path, neo4j_client):
    (tmp_path / "models.py").write_text("class Animal:\n    pass\n")
    (tmp_path / "main.py").write_text("import os\nfrom models import Animal\n")
    root_path = _build(tmp_path, neo4j_client)

    deps = get_file_dependencies(neo4j_client, root_path, "main.py")

    by_module = {d["module"]: d["resolved_file"] for d in deps}
    assert by_module["os"] is None
    assert by_module["models.Animal"] == "models.py"


@requires_neo4j
def test_get_importers_of_file(tmp_path, neo4j_client):
    (tmp_path / "utils.py").write_text("def helper():\n    pass\n")
    (tmp_path / "main.py").write_text("from utils import helper\n")
    root_path = _build(tmp_path, neo4j_client)

    importers = get_importers_of_file(neo4j_client, root_path, "utils.py")

    assert [i["importer"] for i in importers] == ["main.py"]


@requires_neo4j
def test_get_class_ancestors_transitive(tmp_path, neo4j_client):
    (tmp_path / "animals.py").write_text(
        "class Animal:\n    pass\n\nclass Mammal(Animal):\n    pass\n\nclass Dog(Mammal):\n    pass\n"
    )
    root_path = _build(tmp_path, neo4j_client)

    ancestors = get_class_ancestors(neo4j_client, root_path, "animals.py", "Dog")

    assert [a["name"] for a in ancestors] == ["Mammal", "Animal"]
    assert [a["distance"] for a in ancestors] == [1, 2]


@requires_neo4j
def test_find_symbol_across_classes_and_functions(tmp_path, neo4j_client):
    (tmp_path / "a.py").write_text("def process():\n    pass\n")
    (tmp_path / "b.py").write_text("class Handler:\n    def process(self):\n        pass\n")
    root_path = _build(tmp_path, neo4j_client)

    matches = find_symbol(neo4j_client, root_path, "process")

    names = {(m["relative_path"], m["name"]) for m in matches}
    assert ("a.py", "process") in names
    assert ("b.py", "Handler.process") in names
