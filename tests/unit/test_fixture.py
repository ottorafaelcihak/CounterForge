from hashlib import sha256

import pytest
from counterforge_targets.fixture import FixtureTarget
from counterforge_verification.counting import parse_count


@pytest.mark.parametrize(
    "text,want",
    [
        ("5", 5),
        (" 5\n", 5),
        ('{"count":5}', 5),
        ("5 containers", None),
        ("five", None),
        ("4 5", None),
        ("", None),
        ("5.0", None),
        ('{"count":true}', None),
        ('{"count":5.0}', None),
        ('{"count":5,"extra":1}', None),
        ('{"count":4,"count":5}', None),
    ],
)
def test_strict_count_parsing_never_repairs_ambiguous_output(text, want):
    assert parse_count(text) == want


async def test_fixture_is_hash_keyed_and_expected_answer_is_explicit():
    image = b"known-test-image"
    target = FixtureTarget({sha256(image).hexdigest(): "4"}, expected_count=5)
    result = await target.evaluate(image, "Count objects", {})
    assert result.status == "INCORRECT"
    assert result.parsed_answer == 4
    assert result.model_id == "LOCAL_FIXTURE"
    assert result.raw_asset_sha256 == sha256(b"4").hexdigest()
    again = await target.evaluate(image, "Count objects", {})
    assert result == again


async def test_unknown_fixture_hash_does_not_invent_answer():
    result = await FixtureTarget({}, expected_count=5).evaluate(b"unknown", "Count", {})
    assert result.status == "UNKNOWN"
    assert result.parsed_answer is None


async def test_malformed_fixture_answer_cannot_become_count_failure():
    image = b"test"
    target = FixtureTarget({sha256(image).hexdigest(): "4 or 5"}, expected_count=5)
    assert (await target.evaluate(image, "Count", {})).status == "UNKNOWN"
