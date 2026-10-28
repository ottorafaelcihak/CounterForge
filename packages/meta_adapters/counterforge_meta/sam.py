"""Pure SAM normalization; only an injected writer can persist immutable masks."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from counterforge_contracts.providers import SegmentationResult, SegmentedObject

from .transport import MAX_IMAGE_PIXELS, MetaTransport, inspect_image


@dataclass(frozen=True)
class NormalizedMask:
    object_id: str
    bbox_xyxy: tuple[float, float, float, float]
    png_bytes: bytes = field(repr=False)
    confidence_or_null: float | None = None


@dataclass(frozen=True)
class NormalizedSegmentation:
    outcome: Literal["COMPLETED", "INCOMPLETE", "REFUSED", "PROVIDER_ERROR"]
    objects: tuple[NormalizedMask, ...] = ()
    diagnostics: tuple[str, ...] = ()


async def normalize_events(
    events: list[dict[str, Any]] | tuple[dict[str, Any], ...], *, width: int, height: int
) -> NormalizedSegmentation:
    def invalid(code: str) -> NormalizedSegmentation:
        return NormalizedSegmentation("INCOMPLETE", diagnostics=(code,))

    if (
        type(width) is not int
        or type(height) is not int
        or min(width, height) <= 0
        or width * height > MAX_IMAGE_PIXELS
    ):
        return invalid("SAM_SOURCE_GEOMETRY")
    if sys.platform != "linux":
        return invalid("SAM_SANDBOX_UNAVAILABLE")
    data = json.dumps(
        {"events": events, "width": width, "height": height}, allow_nan=False
    ).encode()
    if len(data) > 4 * 1024 * 1024 or len(events) > 4096:
        return invalid("SAM_INPUT_LIMIT")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        str(Path(__file__).with_name("_sam_decode.py")),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={},
        limit=16 * 1024 * 1024 + 1,
    )
    try:
        async with asyncio.timeout(10):
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(data)
            await process.stdin.drain()
            process.stdin.close()
            output = bytearray()
            while chunk := await process.stdout.read(65536):
                output.extend(chunk)
                if len(output) > 16 * 1024 * 1024:
                    return invalid("SAM_OUTPUT_LIMIT")
            await process.wait()
        if process.returncode != 0:
            return invalid("SAM_SANDBOX_FAILED")
        payload = json.loads(output)
        outcome = payload["outcome"]
        if outcome not in {"COMPLETED", "INCOMPLETE", "REFUSED"}:
            return invalid("SAM_INVALID_STREAM")
        objects: list[NormalizedMask] = []
        if outcome == "COMPLETED":
            for item in payload["objects"]:
                png = base64.b64decode(item["png_base64"], validate=True)
                _, w, h = inspect_image(png)
                if (w, h) != (width, height):
                    return invalid("SAM_MASK_GEOMETRY")
                objects.append(
                    NormalizedMask(
                        item["object_id"], tuple(item["bbox_xyxy"]), png, item["confidence_or_null"]
                    )
                )
            if not 1 <= len(objects) <= 16:
                return invalid("SAM_OBJECT_LIMIT")
        return NormalizedSegmentation(outcome, tuple(objects), tuple(payload["diagnostics"]))
    except (TimeoutError, ValueError, KeyError, OSError, TypeError):
        return invalid("SAM_SANDBOX_FAILED")
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


class SamSegmenter:
    def __init__(
        self, *, transport: MetaTransport, mask_writer: Callable[[str, bytes], Awaitable[UUID]]
    ) -> None:
        self.transport, self.mask_writer = transport, mask_writer

    async def segment(self, image_bytes: bytes, concept: str) -> SegmentationResult:
        _, width, height = inspect_image(image_bytes)
        response = await self.transport.sam(image_bytes=image_bytes, concept=concept)
        if response.outcome not in {"COMPLETED", "INCOMPLETE"}:
            return SegmentationResult(
                objects=[],
                outcome="REFUSED" if response.outcome == "REFUSED" else "PROVIDER_ERROR",
                diagnostics=(f"SAM_{response.outcome}",),
            )
        normalized = await normalize_events(response.events, width=width, height=height)
        objects = [
            SegmentedObject(
                object_id=obj.object_id,
                bbox_xyxy=obj.bbox_xyxy,
                mask_asset_id=await self.mask_writer(obj.object_id, obj.png_bytes),
                confidence_or_null=obj.confidence_or_null,
            )
            for obj in normalized.objects
        ]
        return SegmentationResult(
            objects=objects, outcome=normalized.outcome, diagnostics=normalized.diagnostics
        )
