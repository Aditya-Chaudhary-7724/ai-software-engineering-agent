"""Manual, human-readable demonstration of Phase 5 knowledge graph.

Builds a small multi-file synthetic repository, ingests + parses it
(Phases 1-2), builds a Neo4j knowledge graph from it (Phase 5), and
runs a handful of graph queries that demonstrate what a graph can
answer that vector search alone cannot: exact structural traversal
(inheritance chains, import dependencies, reverse lookups).

Requires a reachable Neo4j (NEO4J_URI/USERNAME/PASSWORD in the
environment — see .env.example). Cleans up everything it creates.

Run from the repository root:

    set -a; source .env; set +a
    .venv/bin/python backend/scripts/manual_graph_demo.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.service import IngestionService
from parsing.service import ParsingService

from graph.builder import GraphBuilder
from graph.client import Neo4jClient
from graph.queries import (
    find_symbol,
    get_class_ancestors,
    get_file_dependencies,
    get_importers_of_file,
    get_repository_summary,
)
from graph.schema import apply_constraints


def build_sample_repository(root: Path) -> None:
    (root / "models.py").write_text(
        "class Animal:\n"
        "    def speak(self):\n"
        "        pass\n\n"
        "class Dog(Animal):\n"
        "    def speak(self):\n"
        "        return 'Woof'\n"
    )
    (root / "services" ).mkdir()
    (root / "services" / "shelter.py").write_text(
        "from models import Dog\n\n"
        "def adopt():\n"
        "    return Dog()\n"
    )
    (root / "main.py").write_text(
        "from services.shelter import adopt\n\n"
        "def run():\n"
        "    adopt().speak()\n"
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="graph-demo-") as tmp_dir:
        root = Path(tmp_dir)
        build_sample_repository(root)

        ingestion_result = IngestionService().ingest(str(root))
        parsing_result = ParsingService().parse_repository(str(root), ingestion_result)

        client = Neo4jClient()
        try:
            apply_constraints(client)
            builder = GraphBuilder(client)
            build_result = builder.build(str(root), ingestion_result, parsing_result)

            print(f"Nodes created: {build_result.node_counts}")
            print(f"Relationships created: {build_result.relationship_counts}")
            print()

            root_path = str(root.resolve())

            print("Repository summary (queried live from the graph):")
            print(f"  {get_repository_summary(client, root_path)}")
            print()

            print("What does main.py depend on?")
            for dep in get_file_dependencies(client, root_path, "main.py"):
                resolved = dep["resolved_file"] or "(external / unresolved)"
                print(f"  imports '{dep['module']}' -> {resolved}")
            print()

            print("Who imports models.py?")
            for importer in get_importers_of_file(client, root_path, "models.py"):
                print(f"  {importer['importer']}")
            print()

            print("Inheritance chain of Dog:")
            for ancestor in get_class_ancestors(client, root_path, "models.py", "Dog"):
                print(f"  Dog -> {ancestor['name']} (distance {ancestor['distance']})")
            print()

            print("Every definition of 'speak' in the repository:")
            for match in find_symbol(client, root_path, "speak"):
                print(f"  {match['relative_path']}: {match['name']} [{match['kind']}]")
            print()

            # Clean up everything this demo created.
            client.run("MATCH (n {repository_root_path: $root_path}) DETACH DELETE n", root_path=root_path)
            client.run("MATCH (r:Repository {root_path: $root_path}) DETACH DELETE r", root_path=root_path)
            print("(demo data removed from Neo4j)")
        finally:
            client.close()


if __name__ == "__main__":
    main()
