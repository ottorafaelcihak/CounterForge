import json
from io import BytesIO

from PIL import Image

from services.worker.app.fixtures import FIXTURE_ROOT, seed_bytes


def test_original_development_seed_has_five_distinct_manifest_rectangles():
    rectangles = [
        (40, 60, 96, 176),
        (128, 60, 184, 176),
        (216, 60, 272, 176),
        (304, 60, 360, 176),
        (392, 60, 448, 176),
    ]
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text())
    assert manifest["rectangles_half_open"] == [list(box) for box in rectangles]
    assert manifest["verified_count"] == 5 and manifest["rights"] == "CC0-1.0"
    with Image.open(BytesIO(seed_bytes())) as image:
        assert image.size == (480, 240) and image.mode == "RGB"
        blue, background = (23, 86, 184), (244, 240, 232)
        assert dict(image.getcolors()) == {5 * 56 * 116: blue, 480 * 240 - 5 * 56 * 116: background}
        for box in rectangles:
            assert image.crop(box).getcolors() == [(56 * 116, blue)]
        runs = [
            (x, image.getpixel((x, 100)))
            for x in range(480)
            if x == 0 or image.getpixel((x, 100)) != image.getpixel((x - 1, 100))
        ]
        assert [x for x, color in runs if color == blue] == [40, 128, 216, 304, 392]
        assert [x for x, color in runs if color == background] == [0, 96, 184, 272, 360, 448]
