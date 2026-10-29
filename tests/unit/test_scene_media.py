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
