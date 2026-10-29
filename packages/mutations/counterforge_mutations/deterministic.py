"""Versioned integer transforms. Strength is a parameter, never a safety claim.

Strength is the faithfully recorded family parameter, not a universal monotone
"amount of change" across families. Each family has its own neutral point, and
these mappings are never remapped to make strength look comparable:

- ``brightness_integer_v1``: numerator ``500 + 1000*strength``, so strength 0 is
  0.5x, strength 0.5 is the identity and strength 1 is 1.5x. Change is therefore
  largest at both ends of the range and absent in the middle.
- ``solid_rgb_v1`` (background replacement): level ``1000*strength`` interpolates
  toward the sampled color, so strength 0 is a raster identity and strength 1
  fully replaces the unprotected pixels.
- ``rectangles_v1`` (background clutter): ``1 + level//100`` rectangles, so
  strength 0 already draws one rectangle over the replaced background; there is
  no clutter-free setting in this family.

Phase 05 must read the recorded parameters rather than treating strength as a
severity score, and must not present a neutral-point output as a discovered
target weakness.
"""

import random

from counterforge_contracts.mutations import Parameters
from PIL import Image, ImageDraw


def transform(source: Image.Image, parameters: Parameters, rng_seed: int) -> Image.Image:
    rng = random.Random(rng_seed)
    if parameters.algorithm == "brightness_integer_v1":
        return source.point(
            [min(255, v * parameters.brightness_numerator // 1000) for v in range(256)] * 3
        )
    color = tuple(rng.randrange(256) for _ in range(3))
    if parameters.algorithm == "solid_rgb_v1":
        # Per-channel integer LUT avoids a floating-point blend/rounding policy.
        channels = [
            channel.point(
                [
                    (v * (1000 - parameters.level) + sample * parameters.level) // 1000
                    for v in range(256)
                ]
            )
            for channel, sample in zip(source.split(), color, strict=True)
        ]
        return Image.merge("RGB", tuple(channels))
    result = Image.new("RGB", source.size, color)
    if parameters.algorithm == "rectangles_v1":
        draw = ImageDraw.Draw(result)
        for _ in range(1 + parameters.level // 100):
            x1, x2 = sorted((rng.randrange(source.width), rng.randrange(source.width)))
            y1, y2 = sorted((rng.randrange(source.height), rng.randrange(source.height)))
            draw.rectangle((x1, y1, x2, y2), fill=tuple(rng.randrange(256) for _ in range(3)))
    return result
