"""LOCAL TESTED against the real local Neo4j container (ai-swe-agent-neo4j);
skips cleanly if unreachable (see conftest.py).
"""

from ingestion.service import IngestionService
from parsing.service import ParsingService

from graph.builder import GraphBuilder
from tests.graph.conftest import requires_neo4j


def _build(tmp_path, client):
    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    return GraphBuilder(client).build(str(tmp_path), ingestion_result, parsing_result)


@requires_neo4j
def test_build_creates_repository_and_file_nodes(tmp_path, neo4j_client):
    (tmp_path / "main.py").write_text("def hello():\n    pass\n")

    result = _build(tmp_path, neo4j_client)

    assert result.node_counts["Repository"] == 1
    assert result.node_counts["File"] == 1
    assert result.node_counts["Function"] == 1


@requires_neo4j
def test_build_creates_folder_hierarchy(tmp_path, neo4j_client):
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "lib" / "util.py").write_text("x = 1\n")

    result = _build(tmp_path, neo4j_client)

    assert result.node_counts["Folder"] == 2  # "src" and "src/lib"

    rows = neo4j_client.run(
        "MATCH (r:Repository {root_path: $root_path})-[:CONTAINS]->(f:Folder) RETURN f.relative_path AS path",
        root_path=str(tmp_path.resolve()),
    )
    assert [row["path"] for row in rows] == ["src"]


@requires_neo4j
def test_build_links_class_to_method_via_defines(tmp_path, neo4j_client):
    (tmp_path / "animals.py").write_text("class Animal:\n    def speak(self):\n        pass\n")

    _build(tmp_path, neo4j_client)

    rows = neo4j_client.run(
        """
        MATCH (c:Class {repository_root_path: $root_path, name: 'Animal'})-[:DEFINES]->(fn:Function)
        RETURN fn.qualified_name AS name
        """,
        root_path=str(tmp_path.resolve()),
    )
    assert [row["name"] for row in rows] == ["Animal.speak"]


@requires_neo4j
def test_build_creates_inherits_edge_for_unambiguous_base_class(tmp_path, neo4j_client):
    (tmp_path / "animals.py").write_text(
        "class Animal:\n    pass\n\nclass Dog(Animal):\n    pass\n"
    )

    result = _build(tmp_path, neo4j_client)

    assert result.relationship_counts["INHERITS"] == 1
    rows = neo4j_client.run(
        """
        MATCH (:Class {repository_root_path: $root_path, name: 'Dog'})-[:INHERITS]->(parent:Class)
        RETURN parent.name AS name
        """,
        root_path=str(tmp_path.resolve()),
    )
    assert [row["name"] for row in rows] == ["Animal"]


@requires_neo4j
def test_build_skips_inherits_edge_for_ambiguous_base_class(tmp_path, neo4j_client):
    (tmp_path / "a.py").write_text("class Base:\n    pass\n")
    (tmp_path / "b.py").write_text("class Base:\n    pass\n")
    (tmp_path / "c.py").write_text("class Child(Base):\n    pass\n")

    result = _build(tmp_path, neo4j_client)

    # "Base" is defined in two files, so no INHERITS edge is created —
    # but the raw name is still preserved as a property.
    assert result.relationship_counts["INHERITS"] == 0
    rows = neo4j_client.run(
        "MATCH (c:Class {repository_root_path: $root_path, name: 'Child'}) RETURN c.base_class_names AS bases",
        root_path=str(tmp_path.resolve()),
    )
    assert rows[0]["bases"] == ["Base"]


@requires_neo4j
def test_build_creates_module_and_resolves_import(tmp_path, neo4j_client):
    (tmp_path / "models.py").write_text("class Animal:\n    pass\n")
    (tmp_path / "main.py").write_text("from models import Animal\n")

    result = _build(tmp_path, neo4j_client)

    assert result.relationship_counts["IMPORTS"] == 1
    assert result.relationship_counts["RESOLVES_TO"] == 1

    rows = neo4j_client.run(
        """
        MATCH (:File {repository_root_path: $root_path, relative_path: 'main.py'})-[:IMPORTS]->
              (:Module)-[:RESOLVES_TO]->(resolved:File)
        RETURN resolved.relative_path AS path
        """,
        root_path=str(tmp_path.resolve()),
    )
    assert [row["path"] for row in rows] == ["models.py"]


@requires_neo4j
def test_build_external_import_has_no_resolves_to(tmp_path, neo4j_client):
    (tmp_path / "main.py").write_text("import os\n")

    result = _build(tmp_path, neo4j_client)

    assert result.relationship_counts["IMPORTS"] == 1
    assert result.relationship_counts["RESOLVES_TO"] == 0


@requires_neo4j
def test_build_is_idempotent(tmp_path, neo4j_client):
    (tmp_path / "main.py").write_text("def hello():\n    pass\n")

    _build(tmp_path, neo4j_client)
    _build(tmp_path, neo4j_client)  # re-run should not duplicate nodes

    rows = neo4j_client.run(
        "MATCH (f:File {repository_root_path: $root_path, relative_path: 'main.py'}) RETURN count(f) AS c",
        root_path=str(tmp_path.resolve()),
    )
    assert rows[0]["c"] == 1


@requires_neo4j
def test_build_empty_repository(tmp_path, neo4j_client):
    result = _build(tmp_path, neo4j_client)

    assert result.node_counts["File"] == 0
    assert result.node_counts["Class"] == 0
    assert result.node_counts["Function"] == 0
