import pytest

from services.worker.app.random_strategy import RANDOM_REVISION, random_plan

FAMILIES = ["background_replacement", "background_clutter", "brightness_shift"]


def test_recorded_random_seed_reproduces_the_plan_and_full_available_range():
    first = random_plan(20261030, FAMILIES, 0.01)
    assert first == random_plan(20261030, FAMILIES, 0.01)
    assert first.family == "brightness_shift" and first.strength == 0.2198
    plans = [random_plan(seed, FAMILIES, 0.01) for seed in range(500)]
    assert {p.family for p in plans} == set(FAMILIES)
    assert min(p.strength for p in plans) < 0.1
    assert max(p.strength for p in plans) > 0.9
    assert all(0 <= p.strength <= 1 for p in plans)
    assert all(
        p.estimated_cost_usd == (0 if p.family == "brightness_shift" else 0.01) for p in plans
    )
    assert RANDOM_REVISION == "random-initial-v2"


def test_random_families_cannot_escape_the_contract():
    assert random_plan(7, ["brightness_shift"], 0.01).family == "brightness_shift"
    with pytest.raises(ValueError):
        random_plan(7, [], 0.01)
    with pytest.raises(ValueError):
        random_plan(7, ["brightness_shift", "brightness_shift"], 0.01)
