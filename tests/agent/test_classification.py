import pytest

from agent.classification import classify_task


@pytest.mark.parametrize(
    "question",
    ["What does the login function do?", "Explain how authentication works", "Where is the config loaded?"],
)
def test_classify_informational_question_as_answer(question):
    assert classify_task(question) == "answer"


@pytest.mark.parametrize(
    "question", ["Fix the bug in login()", "Please refactor this class", "Add a new endpoint", "Rename this function"]
)
def test_classify_change_request_as_modify(question):
    assert classify_task(question) == "modify"


@pytest.mark.parametrize("question", ["Run the tests", "Add a test for login", "Is this covered by testing?"])
def test_classify_test_request_as_test(question):
    assert classify_task(question) == "test"


def test_test_keyword_takes_priority_over_modify_keyword():
    # Contains both "add" (modify) and "test" (test) — test wins.
    assert classify_task("add a test for the login function") == "test"


def test_empty_question_defaults_to_answer():
    assert classify_task("") == "answer"
