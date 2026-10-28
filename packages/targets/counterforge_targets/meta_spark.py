"""Authorized Meta Spark counting target; version remains provider managed."""

from __future__ import annotations

from hashlib import sha256

from counterforge_contracts.providers import TargetResult
from counterforge_contracts.versions import TargetOutcome
from counterforge_meta.transport import MetaTransport, image_data_url
from counterforge_verification.counting import parse_count
from pydantic import JsonValue

TARGET_TEMPLATE = "target-count-v1"


class MetaSparkTarget:
    def __init__(self, *, transport: MetaTransport, expected_count: int) -> None:
        if type(expected_count) is not int or not 0 <= expected_count <= 1000:
            raise ValueError("invalid expected count")
        self.transport, self.expected_count = transport, expected_count

    async def evaluate(
        self, image_bytes: bytes, prompt: str, pinned: dict[str, JsonValue]
    ) -> TargetResult:
        # The API must pin these fixed settings honestly; unimplemented options cannot
        # be silently claimed as honored settings in exported reproduction evidence.
        allowed = {"temperature", "top_p", "max_completion_tokens", "max_output_tokens"}
        if set(pinned) - allowed:
            raise ValueError("unsupported target options")
        if "max_completion_tokens" in pinned and "max_output_tokens" in pinned:
            raise ValueError("conflicting token options")
        tokens = pinned.get("max_output_tokens", pinned.get("max_completion_tokens", 128))
        sampling: dict[str, float] = {}
        for name, maximum in (("temperature", 2.0), ("top_p", 1.0)):
            if name in pinned and pinned[name] is not None:
                value = pinned[name]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not 0 <= value <= maximum
                    or (name == "top_p" and value == 0)
                ):
                    raise ValueError("invalid sampling option")
                sampling[name] = float(value)
        if type(tokens) is not int or not 1 <= tokens <= 4096 or not 1 <= len(prompt) <= 2000:
            raise ValueError("invalid target settings")
        response = await self.transport.chat(
            category="TARGET",
            template_version=TARGET_TEMPLATE,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url(image_bytes)}},
                    ],
                }
            ],
            max_completion_tokens=tokens,
            temperature=sampling.get("temperature"),
            top_p=sampling.get("top_p"),
        )
        raw = ""
        if response.outcome == "COMPLETED":
            try:
                text = response.data["choices"][0]["message"]["content"]
                if isinstance(text, str):
                    raw = text
            except (KeyError, IndexError, TypeError):
                pass
        parsed = parse_count(raw) if response.outcome == "COMPLETED" else None
        status = TargetOutcome.UNKNOWN
        if response.outcome in {"PROVIDER_ERROR", "NOT_DISPATCHED", "RATE_LIMITED"}:
            status = TargetOutcome.PROVIDER_ERROR
        elif parsed is not None:
            status = (
                TargetOutcome.CORRECT if parsed == self.expected_count else TargetOutcome.INCORRECT
            )
        return TargetResult(
            raw_text=raw,
            parsed_answer=parsed,
            status=status,
            model_id="muse-spark-1.3",
            version_claim="provider-managed",
            options={
                **pinned,
                (
                    "max_output_tokens"
                    if "max_output_tokens" in pinned
                    else "max_completion_tokens"
                ): tokens,
            },
            latency_ms=response.metadata.latency_ms,
            request_id=response.metadata.request_id,
            raw_asset_sha256=sha256(raw.encode()).hexdigest(),
        )
