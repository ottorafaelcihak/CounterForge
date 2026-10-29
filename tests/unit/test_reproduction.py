import pytest

from services.worker.app.experiments import classify_confirmation


@pytest.mark.parametrize(
    "outcomes,expected",
    [
        (["INCORRECT"] * 3, "STABLE"),
        (["INCORRECT", "CORRECT", "INCORRECT"], "INTERMITTENT"),
        (["CORRECT"] * 3, "NOT_REPRODUCED"),
        (["INCORRECT"] * 2, "REVIEW_REQUIRED"),
        (["INCORRECT", "UNKNOWN", "INCORRECT"], "REVIEW_REQUIRED"),
        (["INCORRECT", "PROVIDER_ERROR", "INCORRECT"], "REVIEW_REQUIRED"),
    ],
)
def test_fresh_confirmation_classification(outcomes, expected):
    assert classify_confirmation(outcomes) == expected
