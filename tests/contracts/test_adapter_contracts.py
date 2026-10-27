from hashlib import sha256
from uuid import UUID

import pytest
from counterforge_contracts.events import ExperimentEvent
from counterforge_contracts.providers import SegmentationResult, TargetResult
from counterforge_contracts.responses import ErrorEnvelope, Target
from counterforge_contracts.versions import ExperimentState, MutationState
from pydantic import ValidationError

from services.api.app.main import app


def test_unknown_output_cannot_claim_incorrect_count():
    with pytest.raises(ValidationError):
        TargetResult(
            raw_text="uncertain",
            parsed_answer=None,
            status="INCORRECT",
            model_id="LOCAL_FIXTURE",
            version_claim="fixture-v1",
            options={},
            latency_ms=0,
            raw_asset_sha256=sha256(b"uncertain").hexdigest(),
        )


@pytest.mark.parametrize(
    "objects",
    [[], [{"object_id": "a", "bbox_xyxy": [3, 1, 2, 4], "mask_asset_id": str(UUID(int=1))}]],
)
def test_completed_segmentation_requires_nonempty_geometry(objects):
    with pytest.raises(ValidationError):
        SegmentationResult(objects=objects, outcome="COMPLETED")


def test_incomplete_segmentation_is_retained_as_an_explicit_outcome():
    result = SegmentationResult(objects=[], outcome="INCOMPLETE", diagnostics=("truncated",))
    assert result.outcome == "INCOMPLETE"
    assert result.diagnostics == ("truncated",)


def test_public_target_has_no_secret_reference_boundary():
    payload = {
        "id": UUID(int=1),
        "project_id": UUID(int=2),
        "adapter_key": "META_SPARK",
        "model_id": "muse-spark-1.3",
        "model_revision": "provider-managed",
        "inference_options": {},
        "enabled": True,
        "secret_ref": "DO_NOT_PUBLISH",
    }
    with pytest.raises(ValidationError):
        Target.model_validate(payload)


def test_error_details_cannot_contain_provider_objects():
    with pytest.raises(ValidationError):
        ErrorEnvelope.model_validate(
            {
                "error": {
                    "code": "PROVIDER_FAILURE",
                    "message": "failed",
                    "correlation_id": "test",
                    "details": {"headers": {"Authorization": "not-public"}},
                }
            }
        )


def test_event_data_cannot_embed_media_or_headers():
    with pytest.raises(ValidationError):
        ExperimentEvent.model_validate(
            {
                "id": 1,
                "data": {
                    "event": "candidate_validated",
                    "experiment_id": UUID(int=1),
                    "mutation_id": UUID(int=2),
                    "verdict": "VALID",
                    "headers": {"Authorization": "secret"},
                },
            }
        )


def test_python_and_openapi_states_share_complete_membership():
    schemas = app.openapi()["components"]["schemas"]
    assert set(schemas["ExperimentState"]["enum"]) == {item.value for item in ExperimentState}
    assert set(schemas["MutationState"]["enum"]) == {item.value for item in MutationState}
    assert {"PAUSE_REQUESTED", "CANCEL_REQUESTED", "SEED_INVALID_FOR_SEARCH"} <= set(
        schemas["ExperimentState"]["enum"]
    )


def test_openapi_public_dtos_recursively_exclude_internal_provider_fields():
    forbidden = {"secret_ref", "storage_key", "raw_asset_key", "headers", "api_key"}
    for schema in app.openapi()["components"]["schemas"].values():
        assert not (forbidden & schema.get("properties", {}).keys())
