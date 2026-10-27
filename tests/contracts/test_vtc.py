import copy
import json

import pytest
from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.vtc import VisualTestContract
from pydantic import ValidationError


def test_spec_example_preserves_human_answer(vtc_payload):
    model = VisualTestContract.model_validate_json(json.dumps(vtc_payload))
    assert model.expected.value == 5
    assert model.limits.max_target_calls == 60
    assert (
        json.loads(model.model_dump_json())["protected"]["mask_asset_ids"]
        == vtc_payload["protected"]["mask_asset_ids"]
    )


@pytest.mark.parametrize(
    "path,value",
    [
        (("expected", "value"), 4),
        (("expected", "value"), True),
        (("protected", "expected_instances"), 17),
        (("reproduction", "required_failures"), 4),
        (("reproduction", "attempts"), 6),
        (("limits", "max_candidates"), 0),
        (("limits", "max_candidates"), 101),
        (("limits", "max_target_calls"), 2),
        (("limits", "max_target_calls"), 501),
        (("limits", "max_estimated_usd"), 0),
        (("limits", "max_estimated_usd"), 150.01),
        (("limits", "max_estimated_usd"), float("inf")),
        (("limits", "max_runtime_seconds"), 59),
        (("limits", "max_runtime_seconds"), 7201),
        (("protected", "min_visible_fraction"), 0.79),
        (("protected", "min_visible_fraction"), float("nan")),
        (("protected", "mask_asset_ids"), ["not-a-uuid"]),
        (("protected", "mask_asset_ids"), ["00000000-0000-0000-0000-000000000001"] * 5),
        (("schema_version",), "2.0.0"),
        (("task_type",), "ocr"),
        (("expected", "reviewer"), " "),
        (("prompt",), "too short"),
        (("allowed_mutations",), []),
        (("allowed_mutations",), ["object_removal"]),
    ],
)
def test_rejects_invalid_contract_fields(vtc_payload, path, value):
    payload = copy.deepcopy(vtc_payload)
    nested = payload
    for key in path[:-1]:
        nested = nested[key]
    nested[path[-1]] = value
    with pytest.raises(ValidationError):
        VisualTestContract.model_validate(payload)


@pytest.mark.parametrize(
    "section", [None, "expected", "protected", "validation_policy", "reproduction", "limits"]
)
def test_rejects_extra_fields_at_every_nested_boundary(vtc_payload, section):
    target = vtc_payload if section is None else vtc_payload[section]
    target["secret_ref"] = "untrusted"
    with pytest.raises(ValidationError):
        VisualTestContract.model_validate(vtc_payload)


def test_zero_object_case_needs_a_future_contract(vtc_payload):
    vtc_payload["expected"]["value"] = 0
    vtc_payload["protected"]["expected_instances"] = 0
    with pytest.raises(ValidationError):
        VisualTestContract.model_validate(vtc_payload)


def test_browser_integer_form_numbers_match_json_schema(vtc_payload):
    # JavaScript JSON.stringify(10.0) emits 10, which remains a valid JSON number.
    vtc_payload["limits"]["max_estimated_usd"] = 10
    vtc_payload["protected"]["min_visible_fraction"] = 1
    contract = VisualTestContract.model_validate_json(json.dumps(vtc_payload))
    assert contract.limits.max_estimated_usd == 10.0
    assert contract.protected.min_visible_fraction == 1.0
    proposal = plan(strength=1, estimated_cost_usd=0)
    assert proposal.strength == 1.0
    assert proposal.estimated_cost_usd == 0.0


def plan(**changes):
    return MutationPlan.model_validate(
        {
            "schema_version": "1.0.0",
            "hypothesis": "Background clutter may affect counting",
            "family": "background_clutter",
            "strength": 0.35,
            "prompt": "Add background clutter without target objects",
            "target_region": "background_only",
            "requires_muse_image": True,
            "estimated_cost_usd": 0.02,
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"strength": -0.01},
        {"strength": 1.01},
        {"strength": float("nan")},
        {"estimated_cost_usd": -1},
        {"estimated_cost_usd": float("inf")},
        {"target_region": "protected_objects"},
        {"family": "object_removal"},
        {"prompt": "x" * 2001},
        {"unknown": "field"},
    ],
)
def test_rejects_unsafe_mutation_plans(changes):
    with pytest.raises(ValidationError):
        plan(**changes)


def test_plan_cannot_exceed_vtc_family_allowlist(vtc_payload):
    contract = VisualTestContract.model_validate(vtc_payload)
    contract.allowed_mutations = ["brightness_shift"]
    with pytest.raises(ValueError, match="allowed"):
        plan().validate_for_contract(contract)


def test_deterministic_brightness_can_target_full_image():
    value = plan(family="brightness_shift", requires_muse_image=False, target_region="full_image")
    assert value.target_region == "full_image"


def test_generated_scene_cannot_target_full_image():
    with pytest.raises(ValidationError):
        plan(target_region="full_image", requires_muse_image=True)
