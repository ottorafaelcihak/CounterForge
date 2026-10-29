import io

import pytest
from PIL import Image

from services.api.app.artifacts import InvalidImage, normalize_image


def png(mode="RGB", size=(12, 8), color="red"):
    b = io.BytesIO()
    Image.new(mode, size, color).save(b, "PNG")
    return b.getvalue()


def test_strict_scene_rejects_transparency_and_dimension_mismatch():
    with pytest.raises(InvalidImage):
        normalize_image(
            png("RGBA", color=(1, 2, 3, 0)), "image/png", strict_opaque=True, expected_size=(12, 8)
        )
    with pytest.raises(InvalidImage):
        normalize_image(png(), "image/png", strict_opaque=True, expected_size=(13, 8))
    assert (
        normalize_image(png(), "image/png", strict_opaque=True, expected_size=(12, 8)).width == 12
    )
    assert normalize_image(png("RGBA", color=(1, 2, 3, 0)), "image/png").width == 12


def test_strict_scene_rejects_truncated_and_multiframe_media():
    with pytest.raises(InvalidImage):
        normalize_image(png()[:40], "image/png", strict_opaque=True, expected_size=(12, 8))
    b = io.BytesIO()
    Image.new("RGB", (12, 8), "red").save(
        b,
        "PNG",
        save_all=True,
        append_images=[Image.new("RGB", (12, 8), "blue")],
        duration=100,
        loop=0,
    )
    with pytest.raises(InvalidImage):
        normalize_image(b.getvalue(), "image/png", strict_opaque=True, expected_size=(12, 8))
    with pytest.raises(InvalidImage):
        normalize_image(
            b"x" * (10 * 1024 * 1024 + 1), "image/png", strict_opaque=True, expected_size=(12, 8)
        )


def test_background_geometry_is_explicit_deterministic_cover_in_decoder():
    from services.api.app.artifacts import normalize_background

    image = Image.new("RGB", (24, 8), "red")
    for x in range(8, 16):
        for y in range(8):
            image.putpixel((x, y), (0, 0, 255))
    body = io.BytesIO()
    image.save(body, "PNG")
    mapped = normalize_background(body.getvalue(), "image/png", size=(8, 8))
    assert (mapped.width, mapped.height) == (8, 8)
    assert (mapped.original_width, mapped.original_height) == (24, 8)
    assert mapped.policy.startswith("background-cover-center-bicubic-v1")
    assert mapped.png == normalize_background(body.getvalue(), "image/png", size=(8, 8)).png
    with Image.open(io.BytesIO(mapped.png)) as result:
        assert result.getcolors() == [(64, (0, 0, 255))]
    with pytest.raises(InvalidImage):
        normalize_background(png("RGBA", color=(0, 0, 0, 0)), "image/png", size=(8, 8))
