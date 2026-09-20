"""Unit tests for the availability-check helpers themselves — proving
they degrade to False cleanly rather than raising, which is the one
property `evaluation.suite` actually depends on.
"""

from evaluation.environment import docker_available, neo4j_available, postgres_available


def test_postgres_available_returns_false_for_unreachable_host():
    assert postgres_available("postgresql://nobody@127.0.0.1:1/does_not_exist") is False


def test_docker_available_returns_a_bool():
    assert isinstance(docker_available(), bool)


def test_neo4j_available_returns_a_bool():
    assert isinstance(neo4j_available(), bool)
