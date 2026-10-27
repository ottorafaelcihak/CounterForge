"""Typed, offline provider fixtures pin the shared adapter boundary."""

from typing import Any
from uuid import UUID

from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.providers import SegmentationResult, SegmentedObject
from counterforge_contracts.versions import MutationFamily
from counterforge_contracts.vtc import Limits, VisualTestContract
from counterforge_meta.protocols import ExperimentPlanner, SceneGenerator, Segmenter
from counterforge_targets.fixture import FixtureTarget
from counterforge_targets.protocol import TargetModel
from pydantic import JsonValue


class RecordedPlanner:
    async def plan(
        self,
        contract: VisualTestContract,
        history: list[dict[str, JsonValue]],
        remaining_budget: Limits,
    ) -> list[MutationPlan]:
        return [
            MutationPlan(
                hypothesis="Recorded fixture proposal",
                family=MutationFamily.BACKGROUND_CLUTTER,
                strength=0.35,
                prompt="Change background without adding target objects",
                requires_muse_image=True,
                estimated_cost_usd=0.02,
            )
        ]


class RecordedSegmenter:
    async def segment(self, image_bytes: bytes, concept: str) -> SegmentationResult:
        return SegmentationResult(
            objects=[
                SegmentedObject(
                    object_id="fixture-object-1",
                    bbox_xyxy=(0.0, 0.0, 1.0, 1.0),
                    mask_asset_id=UUID(int=1),
                )
            ],
            outcome="COMPLETED",
        )


class RecordedScene:
    async def create_scene(self, source_bytes: bytes, instruction: str) -> bytes:
        return b"fixture-scene-bytes"


async def test_normalized_fixture_results_match_shared_protocols(
    vtc_payload: dict[str, Any],
) -> None:
    # These assignments are also checked by mypy; changing a protocol breaks its fixture.
    planner: ExperimentPlanner = RecordedPlanner()
    segmenter: Segmenter = RecordedSegmenter()
    generator: SceneGenerator = RecordedScene()
    target: TargetModel = FixtureTarget({}, expected_count=5)
    contract = VisualTestContract.model_validate(vtc_payload)
    plans = await planner.plan(contract, [], Limits())
    assert plans[0].validate_for_contract(contract).family == "background_clutter"
    masks = await segmenter.segment(b"fixture-input", "container")
    assert masks.outcome == "COMPLETED"
    assert masks.objects[0].mask_asset_id == UUID(int=1)
    assert (
        await generator.create_scene(b"fixture-input", "background only") == b"fixture-scene-bytes"
    )
    assert (await target.evaluate(b"unknown-fixture", contract.prompt, {})).status == "UNKNOWN"
