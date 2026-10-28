import hashlib
import io
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from PIL import Image

from services.api.app.artifacts import BlobStore, IntegrityError, InvalidImage, normalize_image


def image_bytes(mode="RGB", size=(12, 8), format="PNG", **options):
    output = io.BytesIO()
    Image.new(mode, size, color=0).save(output, format=format, **options)
    return output.getvalue()


def test_normalization_preserves_source_hash_and_strips_metadata():
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private metadata"
    source = image_bytes(size=(3, 2), format="JPEG", exif=exif)
    result = normalize_image(source, "image/jpeg")
    assert result.original_sha256 == hashlib.sha256(source).hexdigest()
    assert result.normalized_sha256 == hashlib.sha256(result.png).hexdigest()
    assert result.original_sha256 != result.normalized_sha256
    with Image.open(io.BytesIO(result.png)) as normalized:
        assert normalized.mode == "RGB" and normalized.size == (2, 3)
        assert not normalized.getexif() and "icc_profile" not in normalized.info
        assert result.pixel_sha256 == hashlib.sha256(normalized.tobytes()).hexdigest()
    assert result.policy == "exif-rgb-png-strip-metadata-v1"


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "L", "P"])
def test_supported_modes_normalize_to_rgb(mode):
    result = normalize_image(image_bytes(mode), "image/png")
    with Image.open(io.BytesIO(result.png)) as image:
        assert image.mode == "RGB" and image.size == (12, 8)


@pytest.mark.parametrize(
    "data,media",
    [
        (b"", "image/png"),
        (b"not an image", "image/png"),
        (image_bytes(), "image/jpeg"),
        (image_bytes()[:30], "image/png"),
        (image_bytes(format="GIF"), "image/gif"),
        (image_bytes() + b"0" * (10 * 1024 * 1024), "image/png"),
    ],
)
def test_invalid_uploads_fail_closed(data, media):
    with pytest.raises(InvalidImage):
        normalize_image(data, media)


def test_dimensions_are_checked_before_decoding_large_raster():
    data = image_bytes(size=(4001, 4000))
    with pytest.raises(InvalidImage):
        normalize_image(data, "image/png")


def test_animated_images_are_rejected():
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        output, format="PNG", save_all=True, append_images=[Image.new("RGB", (2, 2), "blue")]
    )
    with pytest.raises(InvalidImage):
        normalize_image(output.getvalue(), "image/png")


def test_blob_round_trip_and_dedup_are_content_addressed(tmp_path):
    store = BlobStore(tmp_path)
    project = uuid4()
    data = image_bytes()
    ref = store.put(project, data, "image/png")
    digest = hashlib.sha256(data).hexdigest()
    assert ref.storage_key == f"projects/{project}/sha256/{digest[:2]}/{digest}.png"
    assert store.put(project, data, "image/png") == ref
    assert store.read_verified(ref) == data
    other = store.put(uuid4(), data, "image/png")
    assert other.storage_key != ref.storage_key


def test_checksum_size_and_traversal_fail_without_serving_data(tmp_path):
    store = BlobStore(tmp_path)
    ref = store.put(uuid4(), image_bytes(), "image/png")
    with pytest.raises(IntegrityError):
        store.read_verified(replace(ref, storage_key="../outside.png"))
    with pytest.raises(IntegrityError):
        store.read_verified(replace(ref, byte_size=ref.byte_size + 1))
    (tmp_path / ref.storage_key).write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        store.read_verified(ref)
    with pytest.raises(IntegrityError):
        store.put(ref.project_id, image_bytes(), "image/png")


def test_symlinked_directory_and_leaf_are_never_followed(tmp_path):
    root, outside = tmp_path / "assets", tmp_path / "outside"
    outside.mkdir()
    project = uuid4()
    (root / "projects").mkdir(parents=True)
    (root / "projects" / str(project)).symlink_to(outside, target_is_directory=True)
    with pytest.raises(IntegrityError):
        BlobStore(root).put(project, image_bytes(), "image/png")
    assert list(outside.iterdir()) == []
    store = BlobStore(tmp_path / "safe")
    ref = store.put(uuid4(), image_bytes(), "image/png")
    path = store.root / ref.storage_key
    path.unlink()
    path.symlink_to(outside / "missing")
    with pytest.raises(IntegrityError):
        store.put(ref.project_id, image_bytes(), "image/png")
    with pytest.raises(IntegrityError):
        store.read_verified(ref)
    assert list(outside.iterdir()) == []


def test_orphan_cleanup_is_explicit_and_preserves_references(tmp_path):
    store = BlobStore(tmp_path)
    kept = store.put(uuid4(), image_bytes(), "image/png")
    orphan = store.put(uuid4(), image_bytes(), "image/png")
    for ref in (kept, orphan):
        os.utime(tmp_path / ref.storage_key, (1, 1))
    cutoff = datetime.now(UTC) - timedelta(days=1)
    assert store.orphans({kept.storage_key}, cutoff) == [orphan.storage_key]
    assert store.read_verified(orphan)
    assert store.orphans({kept.storage_key}, cutoff, delete=True) == [orphan.storage_key]
    assert store.read_verified(kept)
    assert not (tmp_path / orphan.storage_key).exists()


def test_transparent_pixels_are_composited_on_a_documented_white_matte():
    output = io.BytesIO()
    Image.new("RGBA", (2, 2), (255, 0, 0, 0)).save(output, "PNG")
    normalized = normalize_image(output.getvalue(), "image/png")
    with Image.open(io.BytesIO(normalized.png)) as image:
        assert image.getpixel((0, 0)) == (255, 255, 255)
