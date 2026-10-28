"""Bounded Linux image decoder subprocess; stdin/stdout contain media, never paths."""

import hashlib
import io
import json
import resource
import sys
import warnings

from PIL import Image, ImageOps, features


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
                normalized = Image.new("RGB", oriented.size, "white")
                normalized.paste(rgba, mask=rgba.getchannel("A"))
            else:
                normalized = oriented.convert("RGB")
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
            sys.stdout.buffer.write(json.dumps(header).encode() + b"\n" + output.getvalue())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Decoder errors may contain input metadata. Never return them to the parent.
        sys.exit(1)
