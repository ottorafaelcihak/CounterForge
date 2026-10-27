from typing import Protocol

from counterforge_contracts.providers import TargetResult
from pydantic import JsonValue


class TargetModel(Protocol):
    async def evaluate(
        self, image_bytes: bytes, prompt: str, pinned: dict[str, JsonValue]
    ) -> TargetResult: ...
