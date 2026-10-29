import io
from dataclasses import replace

import pytest
from PIL import Image

from tests.unit.test_mutations import png, scene


def test_binary_compositor_copies_boundary_and_interior_exactly(vtc_payload):
    from counterforge_mutations.compositor import composite_background

    _, seed, masks = scene(vtc_payload)
    result = composite_background(seed.png, png(Image.new("RGB", (9, 9), "white")), masks)
    output = Image.open(io.BytesIO(result.png))
    assert output.getpixel((2, 2)) == (10, 100, 200)
    assert output.getpixel((0, 0)) == (255, 255, 255)
    assert result.metrics.protected_changed == 0
    assert result.metrics.interior_pixels == 9
    assert result.metrics.inner_ring_pixels == 16
    assert result.metrics.outer_ring_changed == 24


@pytest.mark.parametrize("kind", ["size", "soft", "empty", "alpha", "hash", "unapproved"])
def test_compositor_rejects_invalid_masks_and_dimensions(vtc_payload, kind):
    from counterforge_mutations.compositor import composite_background

    _, seed, masks = scene(vtc_payload)
    background = png(Image.new("RGB", (9, 9)))
    if kind == "size":
        background = png(Image.new("RGB", (8, 9)))
    elif kind == "hash":
        masks = (replace(masks[0], sha256="0" * 64),)
    elif kind == "unapproved":
        masks = (replace(masks[0], approved_by=""),)
    else:
        from hashlib import sha256

        image = Image.new(
            "RGBA" if kind == "alpha" else "L",
            (9, 9),
            (255, 255, 255, 0) if kind == "alpha" else (128 if kind == "soft" else 0),
        )
        data = png(image)
        masks = (replace(masks[0], png=data, sha256=sha256(data).hexdigest()),)
    with pytest.raises(ValueError):
        composite_background(seed.png, background, masks)


def test_decoder_checks_integrity_before_lazy_exif_loading():
    from counterforge_mutations.compositor import decode_png

    image = Image.new("RGB", (3, 2), (1, 2, 3))
    assert decode_png(png(image)).getpixel((0, 0)) == (1, 2, 3)
    with pytest.raises(ValueError):
        decode_png(png(image)[:-10])


def test_encoder_strips_metadata_even_after_color_conversion():
    from counterforge_mutations.compositor import encode_png

    image = Image.new("RGB", (3, 2), "red")
    image.info["icc_profile"] = b"not-a-profile"
    image.info["exif"] = b"Exif\x00\x00MM\x00*\x00\x00\x00\x08\x00\x00\x00\x00\x00\x00"
    encoded = encode_png(image.convert("RGB"))
    with Image.open(io.BytesIO(encoded)) as decoded:
        assert not decoded.info
        assert decoded.tobytes() == image.tobytes()


def test_duplicate_geometry_cannot_represent_two_distinct_instances(vtc_payload):
    from uuid import UUID

    from counterforge_mutations.compositor import composite_background

    _, seed, masks = scene(vtc_payload)
    with pytest.raises(ValueError, match="overlap"):
        composite_background(
            seed.png,
            png(Image.new("RGB", (9, 9))),
            (masks[0], replace(masks[0], asset_id=UUID(int=2))),
        )


def test_thin_edge_object_is_preserved_with_measured_ring(vtc_payload):
    from hashlib import sha256

    from counterforge_mutations.compositor import composite_background

    _, seed, masks = scene(vtc_payload)
    mask = Image.new("L", (9, 9))
    for y in range(9):
        mask.putpixel((0, y), 255)
    data = png(mask)
    result = composite_background(
        seed.png,
        png(Image.new("RGB", (9, 9), "white")),
        (replace(masks[0], png=data, sha256=sha256(data).hexdigest()),),
    )
    assert result.metrics.interior_pixels == 0
    assert result.metrics.inner_ring_pixels == 9
    assert result.metrics.inner_ring_changed == 0
    assert result.metrics.outer_ring_pixels == 9


@pytest.mark.parametrize("kind", ["animated", "orientation", "oversized", "invalid", "transparent"])
def test_decoder_rejects_non_normalized_or_unbounded_media(kind, monkeypatch):
    from counterforge_mutations import compositor

    image = Image.new("RGB", (3, 3), "red")
    if kind == "animated":
        out = io.BytesIO()
        image.save(out, "PNG", save_all=True, append_images=[Image.new("RGB", (3, 3), "blue")])
        data = out.getvalue()
    elif kind == "orientation":
        exif = Image.Exif()
        exif[274] = 6
        out = io.BytesIO()
        image.save(out, "PNG", exif=exif)
        data = out.getvalue()
    elif kind == "oversized":
        monkeypatch.setattr(compositor, "MAX_PIXELS", 4)
        data = png(image)
    elif kind == "transparent":
        data = png(Image.new("RGBA", (3, 3), (255, 0, 0, 0)))
    else:
        data = b"invalid"
    with pytest.raises(ValueError):
        compositor.decode_png(data)
