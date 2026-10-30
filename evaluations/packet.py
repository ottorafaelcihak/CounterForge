"""Usable blinded review packet: real image bytes hash-bound to candidates.

The reviewer packet contains only blinded candidate identifiers, evidence
hashes and image files. Strategy, family, verdict and finding status live
only in a separate sealed private mapping that reviewers never receive.
Supplied media is verified against the frozen candidate hash before copying;
a wrong or missing image fails closed and no packet is promoted.
Candidate IDs and manifest member paths are treated as untrusted input:
traversal, absolute paths and unsafe IDs are rejected. Images must decode
as bounded PNG/JPEG/WebP, not merely hash correctly. Packets are immutable:
an existing reviewed packet is never overwritten. Comparison images are
only included when the caller supplies their expected sha256 from source
evidence; otherwise the packet is explicitly candidate-only.
"""

from __future__ import annotations

import re
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from .integrity import atomic_write, canonical, seal, verify

BLINDED_KEYS = ("candidate_id", "candidate_sha256", "image_sha256", "evidence_sha256")
FORBIDDEN_KEYS = {
    "strategy",
    "family",
    "original_verdict",
    "current_verdict",
    "automatic_verdict",
    "stable",
    "showcase",
}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_IMAGE_BYTES = 20 * 1024 * 1024
# Shared bounded-decode policy with the artifact store: 4096 per side, 16 MP.
MAX_DIMENSION = 4096
MAX_PIXELS = 16_000_000
_FORMAT_MEDIA = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def _safe_id(candidate_id: Any) -> str:
    if (
        not isinstance(candidate_id, str)
        or not SAFE_ID.match(candidate_id)
        or ".." in candidate_id
        or candidate_id in {".", ".."}
    ):
        raise ValueError(f"Unsafe candidate ID for review packet: {candidate_id!r}")
    return candidate_id


def _suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp"} else ".png"


def _check_image(body: bytes, label: str) -> None:
    if not body or len(body) > MAX_IMAGE_BYTES:
        raise ValueError(f"Review image size out of bounds for {label}")
    try:
        with Image.open(BytesIO(body)) as image:
            image_format = image.format
            width, height = image.width, image.height
    except Exception as exc:
        raise ValueError(f"Review image does not decode for {label}") from exc
    if image_format not in _FORMAT_MEDIA:
        raise ValueError(f"Unsupported review image format for {label}")
    if not (1 <= width <= MAX_DIMENSION and 1 <= height <= MAX_DIMENSION):
        raise ValueError(f"Review image dimensions out of bounds for {label}")
    if width * height > MAX_PIXELS:
        raise ValueError(f"Review image dimensions out of bounds for {label}")
    # Full bounded decode in the isolated artifact decoder: Image.verify()
    # alone does not decode JPEG scan data, so truncated files passed.
    # Original bytes stay hash-bound in the packet; only validity is checked.
    from services.api.app.artifacts import InvalidImage, normalize_image

    try:
        normalize_image(body, _FORMAT_MEDIA[image_format])
    except InvalidImage as exc:
        raise ValueError(f"Review image does not decode for {label}") from exc


def _member_path(packet_dir: Path, member: Any) -> Path:
    if not isinstance(member, str) or not member or Path(member).is_absolute():
        raise ValueError("Packet member path must be a relative path")
    path = (packet_dir / member).resolve()
    if not path.is_relative_to(packet_dir.resolve()):
        raise ValueError("Packet member path escapes the packet directory")
    return path


def _resolve_media(candidate_id: str, media: dict[str, Path]) -> Path:
    if candidate_id in media:
        return Path(media[candidate_id])
    raise ValueError(f"Missing review image for candidate {candidate_id}")


def build_packet(
    sample: dict[str, Any],
    candidates: list[dict[str, Any]],
    media: dict[str, Path],
    packet_dir: Path,
    private_path: Path,
    *,
    comparison_media: dict[str, tuple[Path, str]] | None = None,
) -> dict[str, Any]:
    """Copy verified candidate (and optionally hash-bound comparison) images.

    ``media`` maps candidate_id to an explicit local image file whose bytes
    must hash to the frozen ``image_sha256``. ``comparison_media`` maps
    candidate_id to ``(file, expected_sha256)`` from source evidence; the
    file must match that expected hash or the packet fails closed. Without
    it the packet is labeled candidate-only.
    """
    verify(sample)
    packet_dir = Path(packet_dir)
    private_path = Path(private_path)
    if private_path.resolve().is_relative_to(packet_dir.resolve()):
        raise ValueError("Private strategy mapping must live outside the reviewer packet")
    if packet_dir.exists() and any(packet_dir.iterdir()):
        raise ValueError("Reviewed packet is immutable; choose a new packet revision directory")
    by_id = {c["candidate_id"]: c for c in candidates}
    blinded = sample.get("blinded_export")
    if not isinstance(blinded, list) or not blinded:
        raise ValueError("Audit sample has no blinded candidates to packet")
    images_dir = packet_dir / "images"
    entries: list[dict[str, Any]] = []
    staged: list[tuple[Path, bytes]] = []
    for row in blinded:
        candidate_id = _safe_id(row["candidate_id"])
        candidate = by_id.get(candidate_id)
        if candidate is None:
            raise ValueError(f"Packet candidate {candidate_id} missing from source")
        for key in BLINDED_KEYS:
            if row.get(key) != candidate.get(key):
                raise ValueError("Blinded export differs from frozen candidate hashes")
        question = candidate.get("task_question")
        expected = candidate.get("expected_count")
        if (
            not isinstance(question, str)
            or not question.strip()
            or not isinstance(expected, int)
            or isinstance(expected, bool)
            or expected < 0
        ):
            raise ValueError(
                f"Audit packet requires blinded task context for candidate {candidate_id}"
            )
        source = _resolve_media(candidate_id, media)
        if not source.exists():
            raise ValueError(f"Missing review image for candidate {candidate_id}")
        body = source.read_bytes()
        if sha256(body).hexdigest() != candidate["image_sha256"]:
            raise ValueError(f"Review image hash mismatch for candidate {candidate_id}")
        _check_image(body, candidate_id)
        name = f"{candidate_id}{_suffix(source)}"
        staged.append((images_dir / name, body))
        entry: dict[str, Any] = {
            "candidate_id": candidate_id,
            "candidate_sha256": candidate["candidate_sha256"],
            "image_sha256": candidate["image_sha256"],
            "evidence_sha256": candidate["evidence_sha256"],
            "image_file": f"images/{name}",
            "task_question": question,
            "expected_count": expected,
        }
        if comparison_media and candidate_id in comparison_media:
            comp, expected_sha = comparison_media[candidate_id]
            comp = Path(comp)
            if not comp.exists():
                raise ValueError(f"Missing comparison image for candidate {candidate_id}")
            comp_body = comp.read_bytes()
            if sha256(comp_body).hexdigest() != expected_sha:
                raise ValueError(
                    f"Comparison image differs from source-evidence hash for {candidate_id}"
                )
            _check_image(comp_body, candidate_id)
            comp_name = f"{candidate_id}.comparison{_suffix(comp)}"
            staged.append((images_dir / comp_name, comp_body))
            entry["comparison_file"] = f"images/{comp_name}"
            entry["comparison_sha256"] = expected_sha
        entries.append(entry)
    entries.sort(key=lambda e: e["candidate_id"])
    manifest = seal(
        dict(
            format_version="1.0",
            kind="blinded-review-packet",
            scope="with-comparison" if comparison_media else "candidate-only",
            sampling_seed=sample.get("sampling_seed"),
            entries=entries,
        )
    )
    for entry in entries:
        if FORBIDDEN_KEYS & set(entry):
            raise ValueError("Reviewer packet would leak strategy identity")
    for path, body in staged:
        atomic_write(path, body)
    atomic_write(packet_dir / "packet-manifest.json", canonical(manifest))
    atomic_write(
        packet_dir / "review-form.json",
        canonical(
            dict(
                instructions=(
                    "Answer the task question in each entry and record the count "
                    "you observe. Judge count/visibility only. Do not infer "
                    "strategy; it is withheld."
                ),
                fields=[
                    "candidate_id",
                    "observed_count",
                    "judgment:PASS|FAIL|AMBIGUOUS",
                    "rationale",
                    "reviewer",
                    "reviewed_at",
                ],
                candidate_ids=[e["candidate_id"] for e in entries],
            )
        ),
    )
    private = seal(
        dict(
            kind="private-strategy-mapping",
            mapping=[
                dict(
                    candidate_id=c["candidate_id"],
                    strategy=c.get("strategy"),
                    family=c.get("family"),
                    original_verdict=c.get("original_verdict"),
                    automatic_verdict=c.get("automatic_verdict"),
                    stable=bool(c.get("stable")),
                    showcase=bool(c.get("showcase")),
                )
                for c in sorted(candidates, key=lambda c: c["candidate_id"])
                if c["candidate_id"] in {e["candidate_id"] for e in entries}
            ],
        )
    )
    atomic_write(private_path, canonical(private))
    return manifest


def verify_packet(packet_dir: Path) -> dict[str, Any]:
    import json

    packet_dir = Path(packet_dir)
    manifest: dict[str, Any] = json.loads((packet_dir / "packet-manifest.json").read_bytes())
    verify(manifest)
    expected_members = {"packet-manifest.json", "review-form.json"}
    for entry in manifest["entries"]:
        expected_members.add(entry["image_file"])
        if "comparison_file" in entry:
            expected_members.add(entry["comparison_file"])
    actual_members = {
        p.relative_to(packet_dir).as_posix() for p in packet_dir.rglob("*") if p.is_file()
    }
    if actual_members != expected_members:
        raise ValueError("Packet contains unexpected or missing members")
    for entry in manifest["entries"]:
        if FORBIDDEN_KEYS & set(entry):
            raise ValueError("Reviewer packet leaks strategy identity")
        _safe_id(entry.get("candidate_id"))
        if not entry.get("task_question") or not isinstance(entry.get("expected_count"), int):
            raise ValueError("Packet entry lacks blinded task context")
        body = _member_path(packet_dir, entry["image_file"]).read_bytes()
        if sha256(body).hexdigest() != entry["image_sha256"]:
            raise ValueError("Packet image hash mismatch")
        _check_image(body, str(entry["candidate_id"]))
        if "comparison_file" in entry:
            comp = _member_path(packet_dir, entry["comparison_file"]).read_bytes()
            if sha256(comp).hexdigest() != entry["comparison_sha256"]:
                raise ValueError("Packet comparison image hash mismatch")
            _check_image(comp, str(entry["candidate_id"]))
    return manifest
