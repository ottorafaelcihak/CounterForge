from collections.abc import Mapping
from hashlib import sha256

from counterforge_contracts.providers import TargetResult
from counterforge_contracts.versions import TargetOutcome
from counterforge_verification.counting import parse_count
from pydantic import JsonValue


class FixtureTarget:
    """Offline fake target. Correctness uses explicit evaluator context, never the prompt."""

    def __init__(self, replies_by_sha256: Mapping[str, str], *, expected_count: int) -> None:
        if type(expected_count) is not int or expected_count < 0:
            raise ValueError("expected_count must be a nonnegative integer")
        self._replies = dict(replies_by_sha256)
        self._expected = expected_count

    async def evaluate(
        self, image_bytes: bytes, prompt: str, pinned: dict[str, JsonValue]
    ) -> TargetResult:
        raw = self._replies.get(sha256(image_bytes).hexdigest(), "")
        parsed = parse_count(raw)
        status = TargetOutcome.UNKNOWN
        if parsed is not None:
            status = TargetOutcome.CORRECT if parsed == self._expected else TargetOutcome.INCORRECT
        return TargetResult(
            raw_text=raw,
            parsed_answer=parsed,
            status=status,
            model_id="LOCAL_FIXTURE",
            version_claim="fixture-v1",
            options=pinned,
            latency_ms=0,
            request_id=None,
            raw_asset_sha256=sha256(raw.encode()).hexdigest(),
        )
