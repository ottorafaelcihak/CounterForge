from io import BytesIO

import pytest
from PIL import Image


def mask_png(mode="L", value=0):
    image = Image.new(mode, (12, 8), value)
    if mode == "L":
        image.paste(255, (2, 1, 8, 6))
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def test_mask_normalization_preserves_binary_source_geometry():
    from services.api.app.masks import decode_mask

    value = decode_mask(mask_png(), width=12, height=8)
    assert value.bbox_xyxy == (2, 1, 8, 6) and value.foreground_pixels == 30
    with Image.open(BytesIO(value.png)) as image:
        assert (
            image.mode == "L"
            and image.size == (12, 8)
            and set(image.get_flattened_data()) == {0, 255}
        )


@pytest.mark.parametrize(
    "data,width,height",
    [
        (mask_png(), 13, 8),
        (mask_png("L", 128), 12, 8),
        (mask_png("RGBA", (255, 255, 255, 0)), 12, 8),
        (mask_png("RGB", "red"), 12, 8),
        (b"not a png", 12, 8),
    ],
)
def test_invalid_masks_never_become_usable_foreground(data, width, height):
    from services.api.app.artifacts import InvalidImage
    from services.api.app.masks import decode_mask

    with pytest.raises(InvalidImage):
        decode_mask(data, width=width, height=height)


def test_canvas_opaque_rgba_is_accepted_but_empty_mask_is_not():
    from services.api.app.artifacts import InvalidImage
    from services.api.app.masks import decode_mask

    assert (
        decode_mask(mask_png("RGBA", (255, 255, 255, 255)), width=12, height=8).foreground_pixels
        == 96
    )
    with pytest.raises(InvalidImage):
        decode_mask(mask_png("RGB", "black"), width=12, height=8)
