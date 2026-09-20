"""Pure unit tests for the benchmark dataset itself — structural
validity of the fixed cases, and that the sample repository actually
contains the content those cases' expectations are derived from. No
service involved.
"""

from evaluation.dataset import AGENT_CASES, RAG_CASES, RETRIEVAL_CASES, build_sample_repository


def test_build_sample_repository_creates_expected_files(tmp_path):
    build_sample_repository(tmp_path)

    assert (tmp_path / "auth_service.py").exists()
    assert (tmp_path / "routes.py").exists()
    assert (tmp_path / "greet.py").exists()
    assert (tmp_path / "test_greet.py").exists()


def test_sample_repository_content_matches_case_expectations(tmp_path):
    build_sample_repository(tmp_path)

    auth_content = (tmp_path / "auth_service.py").read_text()
    routes_content = (tmp_path / "routes.py").read_text()

    assert "login_user" in auth_content
    assert "generate_jwt" in auth_content
    assert "jwt-for-" in auth_content
    assert "login_route" in routes_content
    assert "login_user" in routes_content  # the actual dependency the retrieval cases rely on


def test_retrieval_cases_have_unique_ids_and_nonempty_expectations():
    ids = [c.case_id for c in RETRIEVAL_CASES]
    assert len(ids) == len(set(ids))
    for case in RETRIEVAL_CASES:
        assert case.query.strip()
        assert case.expected_relevant_files
        assert case.k > 0


def test_rag_cases_have_unique_ids_and_nonempty_expectations():
    ids = [c.case_id for c in RAG_CASES]
    assert len(ids) == len(set(ids))
    for case in RAG_CASES:
        assert case.query.strip()
        assert case.expected_evidence_files
        assert case.expected_context_substrings


def test_agent_cases_have_unique_ids_and_valid_task_types():
    ids = [c.case_id for c in AGENT_CASES]
    assert len(ids) == len(set(ids))
    for case in AGENT_CASES:
        assert case.expected_task_type in {"answer", "modify", "test"}
        assert case.expected_decision in {"answer", "modify", "test"}
