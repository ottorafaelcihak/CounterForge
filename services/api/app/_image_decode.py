"""Bounded Linux image decoder subprocess; stdin/stdout contain media, never paths."""

import hashlib
import io
import json
import resource
import sys
import warnings

from PIL import Image, ImageOps, features
from PIL import __version__ as pillow_version


def main() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    data = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
    if not data or len(data) > 10 * 1024 * 1024:
        raise ValueError
    media = sys.argv[1]
    formats = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
    if media not in formats or (media == "image/webp" and not features.check("webp")):
        raise ValueError
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data), formats=[formats[media]]) as image:
            if image.format != formats[media] or image.width * image.height > 16_000_000:
                raise ValueError
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError
            image.verify()
        with Image.open(io.BytesIO(data), formats=[formats[media]]) as image:
            image.load()
            oriented = ImageOps.exif_transpose(image)
            if "A" in oriented.getbands() or "transparency" in oriented.info:
                rgba = oriented.convert("RGBA")
                if (
                    len(sys.argv) > 2
                    and sys.argv[2] == "strict"
                    and rgba.getchannel("A").getextrema() != (255, 255)
                ):
                    raise ValueError
                normalized = Image.new("RGB", oriented.size, "white")
                normalized.paste(rgba, mask=rgba.getchannel("A"))
            else:
                normalized = oriented.convert("RGB")
            geometry_policy = None
            if len(sys.argv) > 3:
                if sys.argv[2:4] != ["strict", "background-cover"] or len(sys.argv) != 6:
                    raise ValueError
                size = (int(sys.argv[4]), int(sys.argv[5]))
                if any(not 1 <= v <= 4096 for v in size) or size[0] * size[1] > 16_000_000:
                    raise ValueError
                normalized = ImageOps.fit(
                    normalized, size, method=Image.Resampling.BICUBIC, centering=(0.5, 0.5)
                )
                geometry_policy = "background-cover-center-bicubic-v1:Pillow-" + pillow_version
            pixels = normalized.tobytes()
            clean = Image.frombytes("RGB", normalized.size, pixels)
            output = io.BytesIO()
            clean.save(output, format="PNG")
            header = {
                "width": clean.width,
                "height": clean.height,
                "original_width": image.width,
                "original_height": image.height,
                "pixel_sha256": hashlib.sha256(pixels).hexdigest(),
            }
            if geometry_policy:
                header["geometry_policy"] = geometry_policy
            sys.stdout.buffer.write(json.dumps(header).encode() + b"\n" + output.getvalue())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Decoder errors may contain input metadata. Never return them to the parent.
        sys.exit(1)
