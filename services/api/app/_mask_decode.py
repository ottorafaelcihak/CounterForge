"""Bounded PNG-only binary mask decoder; no alpha compositing or resizing."""

import io
import json
import resource
import sys
import warnings


def main() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from PIL import Image

    width, height = int(sys.argv[1]), int(sys.argv[2])
    if min(width, height) < 1 or width * height > 16_000_000:
        raise ValueError
    data = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
    if not data or len(data) > 10 * 1024 * 1024:
        raise ValueError
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data), formats=["PNG"]) as source:
            if source.size != (width, height) or getattr(source, "n_frames", 1) != 1:
                raise ValueError
            source.verify()
        with Image.open(io.BytesIO(data), formats=["PNG"]) as source:
            if source.getexif().get(274, 1) != 1:
                raise ValueError
            rgba = source.convert("RGBA")
            if rgba.getchannel("A").getextrema() != (255, 255):
                raise ValueError
            rgb = rgba.convert("RGB")
            colors = rgb.getcolors(3)
            if colors is None or any(
                color not in {(0, 0, 0), (255, 255, 255)} for _, color in colors
            ):
                raise ValueError
            mask = rgb.convert("L")
            bbox = mask.getbbox()
            if bbox is None:
                raise ValueError
            foreground = mask.histogram()[255]
            clean = Image.frombytes("L", mask.size, mask.tobytes())
            output = io.BytesIO()
            clean.save(output, "PNG")
            sys.stdout.buffer.write(
                json.dumps(
                    {
                        "width": width,
                        "height": height,
                        "bbox_xyxy": bbox,
                        "foreground_pixels": foreground,
                    }
                ).encode()
                + b"\n"
                + output.getvalue()
            )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
