"""Frozen feedback policy: engineering estimates, reserved slots and ceilings."""

from math import ceil, sqrt
from uuid import uuid4

import pytest
from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.requests import StartExperiment
from pydantic import ValidationError


def plan(family="brightness_shift", **kwargs):
    return MutationPlan(
        family=family,
        strength=0.2,
        hypothesis="bounded",
        prompt="background",
        requires_muse_image=family != "brightness_shift",
        estimated_cost_usd=0,
        **kwargs,
    )


def test_feedback_is_opt_in_and_adaptive_only():
    args = dict(project_id=uuid4(), contract_id=uuid4(), target_config_id=uuid4())
    assert StartExperiment(**args).search_policy == "ONE_SHOT"
    with pytest.raises(ValidationError, match="ADAPTIVE"):
        StartExperiment(**args, mode="RANDOM_BASELINE", search_policy="FEEDBACK_V1")


def test_random_loop_is_random_baseline_only():
    args = dict(project_id=uuid4(), contract_id=uuid4(), target_config_id=uuid4())
    assert (
        StartExperiment(**args, mode="RANDOM_BASELINE", search_policy="RANDOM_V1").search_policy
        == "RANDOM_V1"
    )
    with pytest.raises(ValidationError, match="RANDOM_BASELINE"):
        StartExperiment(**args, search_policy="RANDOM_V1")
    with pytest.raises(ValidationError, match="RANDOM_BASELINE"):
        StartExperiment(**args, mode="ADAPTIVE", search_policy="RANDOM_V1")


def test_loop_helpers_distinguish_policies():
    from services.worker.app.experiments import (
        feedback_enabled,
        loop_enabled,
        random_loop_enabled,
    )

    assert loop_enabled({"search_policy": "FEEDBACK_V1"})
    assert loop_enabled({"search_policy": "RANDOM_V1"})
    assert not loop_enabled({"search_policy": "ONE_SHOT"})
    assert not loop_enabled({})
    assert feedback_enabled({"search_policy": "FEEDBACK_V1"})
    assert not feedback_enabled({"search_policy": "RANDOM_V1"})
    assert random_loop_enabled({"search_policy": "RANDOM_V1"})
    assert not random_loop_enabled({"search_policy": "FEEDBACK_V1"})


def test_fixed_exploration_slots_cannot_be_reallocated():
    from services.worker.app.adaptive_policy import exploration_slot

    for limit in range(1, 32):
        slots = [i for i in range(limit) if exploration_slot(i)]
        assert slots == list(range(0, limit, 5))
        assert len(slots) == ceil(0.2 * limit)
    assert not exploration_slot(11)  # quota already consumed is never moved here


def test_beta_ranking_unknown_is_not_a_negative_binary_trial(vtc_payload):
    from counterforge_contracts.vtc import VisualTestContract

    from services.worker.app.adaptive_policy import rank_proposals

    history = [
        dict(family="brightness_shift", validity=v, target_outcomes=o)
        for v, o in [
            ("VALID", [{"phase": "SCREENING", "outcome": "INCORRECT"}]),
            ("VALID", [{"phase": "SCREENING", "outcome": "UNKNOWN"}]),
            ("INVALID", []),
        ]
    ]
    ranked = rank_proposals(
        [plan()],
        VisualTestContract.model_validate(vtc_payload),
        history,
        available_usd=1,
        cost_components={"screen": 0.1},
    )
    r = ranked[0]
    assert r.p_valid == pytest.approx(3 / 5)
    assert r.p_failure == pytest.approx(2 / 3)
    assert r.family_trials == 3
    assert r.score == pytest.approx(((3 / 5) * (2 / 3) + 0.1 / sqrt(4)) / 0.1)
    assert r.selected


def test_filter_before_ranking_upper_costs_and_deterministic_ties(vtc_payload):
    from counterforge_contracts.vtc import VisualTestContract

    from services.worker.app.adaptive_policy import rank_proposals

    contract = VisualTestContract.model_validate(vtc_payload)
    proposals = [plan("background_clutter"), plan(), plan()]
    kwargs = dict(
        available_usd=0.2,
        cost_components={"generation": 0.3, "sam": 0.01, "screen": 0.1, "confirmation": 0.03},
    )
    ranks = rank_proposals(proposals, contract, [], **kwargs)
    assert ranks == rank_proposals(proposals, contract, [], **kwargs)
    assert ranks[0].rejection_reason == "UNAFFORDABLE"
    assert [r.proposal_order for r in ranks if r.selected] == [1]
    assert ranks[1].score == pytest.approx(0.35 / 0.14)
    ties = rank_proposals(
        [plan(), plan("background_clutter"), plan("background_clutter")],
        contract,
        [],
        available_usd=1,
        cost_components={"sam": 0.1},
    )
    assert [r.rank for r in ties] == [3, 1, 2]
    assert [r.proposal_order for r in ties if r.selected] == [1]
    from counterforge_contracts.versions import MutationFamily

    contract = contract.model_copy(update={"allowed_mutations": [MutationFamily.BRIGHTNESS_SHIFT]})
    assert (
        rank_proposals([plan("background_clutter")], contract, [], **kwargs)[0].rejection_reason
        == "INVALID_PROPOSAL"
    )
    assert not any(
        r.selected
        for r in rank_proposals(
            proposals, contract, [], available_usd=0, cost_components={"screen": 0.1}
        )
    )
