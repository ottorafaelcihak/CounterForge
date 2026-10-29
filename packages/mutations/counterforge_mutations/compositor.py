"""Exact binary compositing; no resize, feathering, or synthesized foreground."""

import io
import struct
import warnings
import zlib
from collections.abc import Sequence

from counterforge_contracts.mutations import ApprovedMask, CompositeResult, DiffMetrics, digest
from PIL import Image, ImageChops, ImageFilter

MAX_PIXELS = 16_000_000
MAX_BYTES = 32 * 1024 * 1024


def encode_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    # convert/copy retain .info (including ICC); rebuild from pixels only.
    clean = Image.frombytes(image.mode, image.size, image.tobytes())
    clean.save(output, "PNG", optimize=False, compress_level=9)
    return output.getvalue()


def decode_png(data: bytes, *, mask: bool = False, normalized: bool = False) -> Image.Image:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("image byte limit")
    # Explicit chunk boundaries reject trailing payloads across Pillow versions.
    # Normalized generated evidence permits only raster chunks, without requiring
    # byte-for-byte reproduction by a different compressor build.
    offset = 8
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("invalid PNG signature")
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("truncated PNG chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        end = offset + 12 + length
        tag = data[offset + 4 : offset + 8]
        if (
            end > len(data)
            or zlib.crc32(data[offset + 4 : end - 4]) != struct.unpack(">I", data[end - 4 : end])[0]
        ):
            raise ValueError("invalid PNG chunk")
        if normalized and tag not in {b"IHDR", b"IDAT", b"IEND"}:
            raise ValueError("generated PNG must be metadata free")
        offset = end
        if tag == b"IEND":
            if length != 0 or offset != len(data):
                raise ValueError("trailing PNG payload")
            break
    else:
        raise ValueError("missing PNG terminator")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=["PNG"]) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("image dimensions")
                # verify must precede getexif/n_frames/load, which may close fp.
                image.verify()
            with Image.open(io.BytesIO(data), formats=["PNG"]) as image:
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("image frames")
                if image.getexif().get(274, 1) != 1:
                    raise ValueError("oriented input must be normalized first")
                rgba = image.convert("RGBA")
                if rgba.getchannel("A").getextrema() != (255, 255):
                    raise ValueError("transparent input")
                rgb = rgba.convert("RGB")
                if mask:
                    colors = rgb.getcolors(3)
                    if colors is None or any(
                        c not in {(0, 0, 0), (255, 255, 255)} for _, c in colors
                    ):
                        raise ValueError("mask must be binary")
                    binary = rgb.convert("L")
                    if binary.getbbox() is None:
                        raise ValueError("empty mask")
                    return binary
                return rgb
    except (
        OSError,
        SyntaxError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise ValueError("invalid PNG") from exc


def protected_union(source_bytes: bytes, masks: Sequence[ApprovedMask]) -> Image.Image:
    source = decode_png(source_bytes)
    union = Image.new("L", source.size)
    if not masks or len({m.asset_id for m in masks}) != len(masks):
        raise ValueError("missing/duplicate masks")
    for mask in masks:
        if (
            not mask.approved_by.strip()
            or digest(mask.png) != mask.sha256
            or mask.source_sha256 != digest(source_bytes)
        ):
            raise ValueError("unapproved or unbound mask")
        decoded = decode_png(mask.png, mask=True)
        if decoded.size != source.size:
            raise ValueError("mask dimensions")
        if ImageChops.darker(union, decoded).getbbox() is not None:
            raise ValueError("instance masks overlap")
        union = ImageChops.lighter(union, decoded)
    return union


def measure_diff(
    source: Image.Image, output: Image.Image, union: Image.Image
) -> tuple[DiffMetrics, bytes]:
    # Padding makes the one-pixel Chebyshev ring well-defined at image edges.
    padded = Image.new("L", (union.width + 2, union.height + 2))
    padded.paste(union, (1, 1))
    interior = padded.filter(ImageFilter.MinFilter(3)).crop(
        (1, 1, union.width + 1, union.height + 1)
    )
    inner = ImageChops.subtract(union, interior)
    outer = ImageChops.subtract(union.filter(ImageFilter.MaxFilter(3)), union)
    diff = ImageChops.difference(source, output)
    channels = diff.split()
    changed = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2]).point(
        lambda v: 255 if v else 0
    )

    def count(mask: Image.Image) -> int:
        return mask.histogram()[255]

    def changed_in(mask: Image.Image) -> int:
        return count(ImageChops.darker(changed, mask))

    metrics = DiffMetrics(
        count(union),
        changed_in(union),
        count(interior),
        changed_in(interior),
        count(inner),
        changed_in(inner),
        count(outer),
        changed_in(outer),
        max(max(i for i, n in enumerate(c.histogram()) if n) for c in channels),
        sum(sum(i * n for i, n in enumerate(c.histogram())) for c in channels),
    )
    return metrics, encode_png(diff)


def composite_background(
    source_bytes: bytes,
    background_bytes: bytes,
    approved_masks: Sequence[ApprovedMask],
    blend_policy: str = "strict_binary_original",
) -> CompositeResult:
    if blend_policy != "strict_binary_original":
        raise ValueError("only strict binary original-pixel compositing is supported")
    source, background = decode_png(source_bytes), decode_png(background_bytes)
    if source.size != background.size:
        raise ValueError("background dimensions")
    union = protected_union(source_bytes, approved_masks)
    output = Image.composite(source, background, union)
    metrics, diff = measure_diff(source, output, union)
    return CompositeResult(encode_png(output), encode_png(union), diff, metrics)
