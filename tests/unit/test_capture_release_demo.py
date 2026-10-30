"""Unit regressions for the deterministic stills capture loader (no browser/ffmpeg)."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.capture_release_demo import encode_webm, load_scenes


def _png(width: int = 2, height: int = 2) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00" * (width * 3) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _write(tmp_path: Path, scenes: list[dict[str, object]], payload: bytes | None = None) -> Path:
    image = tmp_path / "still.png"
    image.write_bytes(payload if payload is not None else _png())
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    for scene in scenes:
        scene.setdefault("image", "still.png")
        scene.setdefault("sha256", digest)
        scene.setdefault("caption", "caption")
        scene.setdefault("provenance", "RECORDED STILLS")
    path = tmp_path / "scenes.json"
    path.write_text(json.dumps({"scenes": scenes}), encoding="utf-8")
    return path


def test_load_scenes_accepts_integer_contiguous(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {"id": "a", "start_seconds": 0, "duration_seconds": 3},
            {"id": "b", "start_seconds": 3, "duration_seconds": 4},
        ],
    )
    scenes = load_scenes(tmp_path, path, expected_duration=7)
    assert [s["duration_seconds"] for s in scenes] == [3, 4]


def test_encoder_rejects_early_input_close_even_when_process_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ClosedInput:
        def write(self, _data: bytes) -> None:
            raise BrokenPipeError

    process = SimpleNamespace(stdin=ClosedInput(), stderr=io.BytesIO(), wait=lambda: 0)
    monkeypatch.setattr("scripts.capture_release_demo.subprocess.Popen", lambda *_a, **_kw: process)
    with pytest.raises(RuntimeError, match="before all frames"):
        encode_webm([(b"jpeg", 2)], tmp_path / "ffmpeg", tmp_path / "demo.webm")


def test_load_scenes_rejects_unsafe_slug(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"id": "../escape", "start_seconds": 0, "duration_seconds": 7}])
    with pytest.raises(ValueError, match="slug"):
        load_scenes(tmp_path, path, expected_duration=7)


def test_load_scenes_rejects_fractional_and_gap(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"id": "a", "start_seconds": 1, "duration_seconds": 7.5}])
    with pytest.raises(ValueError):
        load_scenes(tmp_path, path, expected_duration=7)


def test_load_scenes_rejects_hash_mismatch_and_oversize(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"id": "a", "start_seconds": 0, "duration_seconds": 7}])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["scenes"][0]["sha256"] = "0" * 64
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="sha256"):
        load_scenes(tmp_path, path, expected_duration=7)
    big = _write(
        tmp_path,
        [{"id": "a", "start_seconds": 0, "duration_seconds": 7}],
        payload=_png(5000, 5000),
    )
    with pytest.raises(ValueError, match="pixels"):
        load_scenes(tmp_path, big, expected_duration=7)
