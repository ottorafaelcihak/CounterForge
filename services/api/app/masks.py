"""Strict binary masks use a different policy from photographic seed images."""

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .artifacts import MAX_UPLOAD_BYTES, InvalidImage


@dataclass(frozen=True)
class BinaryMask:
    png: bytes = field(repr=False)
    width: int
    height: int
    bbox_xyxy: tuple[int, int, int, int]
    foreground_pixels: int

    def geometry(self) -> dict[str, object]:
        return {
            "width": self.width,
            "height": self.height,
            "bbox_xyxy": list(self.bbox_xyxy),
            "foreground_pixels": self.foreground_pixels,
            "binary": True,
        }


def decode_mask(data: bytes, *, width: int, height: int) -> BinaryMask:
    if not 0 < len(data) <= MAX_UPLOAD_BYTES or sys.platform != "linux":
        raise InvalidImage("Mask could not be accepted")
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(Path(__file__).with_name("_mask_decode.py")),
                str(width),
                str(height),
            ],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            env={},
        )
        if result.returncode != 0 or len(result.stdout) > MAX_UPLOAD_BYTES + 1024:
            raise ValueError
        header, png = result.stdout.split(b"\n", 1)
        metadata = json.loads(header)
        return BinaryMask(
            png, width, height, tuple(metadata["bbox_xyxy"]), metadata["foreground_pixels"]
        )
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        raise InvalidImage("Mask could not be safely accepted") from None
