from uuid import UUID

import pytest


@pytest.fixture
def vtc_payload():
    return {
        "schema_version": "1.0.0",
        "case_id": "shelf-count-001",
        "task_type": "object_counting",
        "prompt": "How many blue containers are visible? Return only an integer.",
        "expected": {"kind": "integer", "value": 5, "verification": "human", "reviewer": "owner"},
        "protected": {
            "label": "blue container",
            "expected_instances": 5,
            "mask_asset_ids": [str(UUID(int=i)) for i in range(1, 6)],
            "min_visible_fraction": 0.95,
        },
        "allowed_mutations": ["background_replacement", "background_clutter", "brightness_shift"],
        "forbidden_mutations": ["object_addition", "object_removal", "protected_region_repaint"],
        "validation_policy": {"uncertain": "REVIEW_REQUIRED", "require_mask_review": True},
        "reproduction": {"attempts": 3, "required_failures": 3},
        "limits": {
            "max_candidates": 20,
            "max_target_calls": 60,
            "max_estimated_usd": 10.0,
            "max_runtime_seconds": 1200,
        },
    }
