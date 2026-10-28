"""Linux sandbox entrypoint for the official eager SAM parser. No wire grammar here."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import resource
import sys
from collections.abc import AsyncIterator
from io import BytesIO
from typing import Any

# Limits precede imports/decoding of untrusted event data.
if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

from meta_sam_parser import (  # noqa: E402
    CompletedOutcome,
    ResponsesStreamRefusalError,
    SegmentationBoxRecord,
    SegmentationMaskRecord,
    decode_mask_to_raster,
    image_segmentation_format,
    parse_responses_stream,
)
from PIL import Image  # noqa: E402


def invalid(code: str = "SAM_INVALID_STREAM") -> dict[str, Any]:
    return {"outcome": "INCOMPLETE", "objects": [], "diagnostics": [code]}


async def decode(payload: dict[str, Any]) -> dict[str, Any]:
    width, height = payload["width"], payload["height"]
    if (
        type(width) is not int
        or type(height) is not int
        or min(width, height) <= 0
        or width * height > 16 * 1024 * 1024
    ):
        return invalid("SAM_SOURCE_GEOMETRY")

    async def source() -> AsyncIterator[dict[str, Any]]:
        for event in payload["events"]:
            yield event

    try:
        result = await parse_responses_stream(source(), image_segmentation_format()).final_result()
    except ResponsesStreamRefusalError:
        return {"outcome": "REFUSED", "objects": [], "diagnostics": ["SAM_REFUSED"]}
    if not isinstance(result.outcome, CompletedOutcome):
        return invalid("SAM_INCOMPLETE")
    if any(d.severity == "error" for d in result.diagnostics):
        return invalid()
    boxes: dict[str, SegmentationBoxRecord] = {}
    masks: dict[str, SegmentationMaskRecord] = {}
    for record in result.records:
        if isinstance(record, (SegmentationBoxRecord, SegmentationMaskRecord)):
            if record.frame is not None:
                return invalid("SAM_FRAME_NOT_IMAGE")
            if isinstance(record, SegmentationBoxRecord):
                if record.object_id in boxes:
                    return invalid("SAM_DUPLICATE_ID")
                boxes[record.object_id] = record
            else:
                if (
                    record.object_id in masks
                    or record.identity.media != "image"
                    or record.identity.frame_index is not None
                ):
                    return invalid("SAM_DUPLICATE_ID")
                masks[record.object_id] = record
        if len(boxes) > 16 or len(masks) > 16:
            return invalid("SAM_OBJECT_LIMIT")
    if not boxes or boxes.keys() != masks.keys():
        return invalid("SAM_MISSING_INSTANCES")
    objects = []
    total = 0
    for identity, box in boxes.items():
        coordinates = (box.left, box.top, box.right, box.bottom)
        if any(not math.isfinite(c) or int(c) != c for c in coordinates):
            return invalid("SAM_INVALID_BOX")
        left, top, right, bottom = (int(c) for c in coordinates)
        if not 0 <= left < right <= width or not 0 <= top < bottom <= height:
            return invalid("SAM_INVALID_BOX")
        mask = masks[identity]
        bounds = mask.bounds
        if (
            (bounds.left, bounds.top, bounds.right, bounds.bottom) != coordinates
            or mask.mask.width != right - left
            or mask.mask.height != bottom - top
        ):
            return invalid("SAM_MASK_GEOMETRY")
        raster = decode_mask_to_raster(mask.mask)
        if (
            len(raster) != (right - left) * (bottom - top)
            or not any(raster)
            or not set(raster) <= {0, 1}
        ):
            return invalid("SAM_MASK_BINARY_NONEMPTY")
        local = Image.frombytes("L", (right - left, bottom - top), raster).point(lambda v: v * 255)
        full = Image.new("L", (width, height))
        full.paste(local, (left, top))
        buffer = BytesIO()
        full.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode()
        total += len(encoded)
        if total > 15 * 1024 * 1024:
            return invalid("SAM_OUTPUT_LIMIT")
        objects.append(
            {
                "object_id": identity,
                "bbox_xyxy": coordinates,
                "png_base64": encoded,
                "confidence_or_null": box.confidence,
            }
        )
    return {"outcome": "COMPLETED", "objects": objects, "diagnostics": []}


def main() -> None:
    try:
        data = sys.stdin.buffer.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            result = invalid("SAM_INPUT_LIMIT")
        else:
            result = asyncio.run(decode(json.loads(data)))
    except (Exception, MemoryError):
        result = invalid()
    sys.stdout.write(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
