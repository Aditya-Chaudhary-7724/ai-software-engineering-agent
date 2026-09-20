from dataclasses import dataclass, field


@dataclass
class GraphBuildResult:
    repository_root_path: str
    node_counts: dict[str, int] = field(default_factory=dict)
    relationship_counts: dict[str, int] = field(default_factory=dict)
