from typing import Protocol

from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.providers import SegmentationResult
from counterforge_contracts.vtc import Limits, VisualTestContract
from pydantic import JsonValue


class ExperimentPlanner(Protocol):
    async def plan(
        self,
        contract: VisualTestContract,
        history: list[dict[str, JsonValue]],
        remaining_budget: Limits,
    ) -> list[MutationPlan]: ...


class Segmenter(Protocol):
    async def segment(self, image_bytes: bytes, concept: str) -> SegmentationResult: ...


class SceneGenerator(Protocol):
    async def create_scene(self, source_bytes: bytes, instruction: str) -> bytes: ...
