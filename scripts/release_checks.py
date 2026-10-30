"""Offline structural validation for Phase 10 release artifacts.

Validation correctness is not release readiness: this tool never
contacts a network, scanner, provider, or database, and it never
verifies external URLs. External/tool checks must be run and recorded
explicitly by the release coordinator (see docs/submission.md).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
ALLOWED_STATUSES = frozenset(
    {"fixture_verified", "live_verified", "partial_live", "unmet", "pending", "pending_root"}
)
CANONICAL_GATE_IDS = (
    "license-owner-choice",
    "root-secret-dependency-sbom-checks",
    "root-fresh-export-quality-gates",
    "real-stable-failure",
    "sam-live-usable-masks",
    "phase08-measured-evaluation",
    "demo-video",
    "public-access-verification",
    "organizer-rules-verification",
    "publication-submission-authorization",
)
GATE_STATUSES = frozenset({"pending", "pending_root", "unmet", "passed"})
OPEN_GATE_STATUSES = frozenset({"pending", "pending_root", "unmet"})
OPEN_ACCEPTANCE_STATUSES = frozenset({"pending", "pending_root", "partial_live", "unmet"})
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
# Rows whose live claim depends on a genuine stable real-model failure.
STABLE_FAILURE_IDS = frozenset({"PRD-AT-08", "TS-AT-07"})
_ID_RE = re.compile(r"^\|\s*((?:PRD|TS)-AT-\d{2})\s*\|\s*([a-z_]+)\s*\|")
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SCENE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def canonical_ids() -> tuple[str, ...]:
    return tuple(f"PRD-AT-{n:02d}" for n in range(1, 13)) + tuple(
        f"TS-AT-{n:02d}" for n in range(1, 21)
    )


def safe_path(root: Path, relative: str) -> Path | None:
    """Resolve a repo-relative path, rejecting traversal and any symlink component."""
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or not relative:
        return None
    resolved_root = root.resolve()
    current = resolved_root
    for part in candidate.parts:
        current = current / part
        try:
            if current.is_symlink():
                return None
        except OSError:
            return None
    resolved = current.resolve()
    if not resolved.is_relative_to(resolved_root):
        return None
    return resolved


def probe_image(data: bytes) -> tuple[str, int, int] | None:
    """Return (mime, width, height) for PNG/JPEG/WebP headers, else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return ("image/png", int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
        kind = data[12:16]
        if kind == b"VP8X":
            return (
                "image/webp",
                int.from_bytes(data[24:27], "little") + 1,
                int.from_bytes(data[27:30], "little") + 1,
            )
        if kind == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            return ("image/webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
        if kind == b"VP8 " and len(data) >= 30:
            return (
                "image/webp",
                int.from_bytes(data[26:28], "little") & 0x3FFF,
                int.from_bytes(data[28:30], "little") & 0x3FFF,
            )
        return None
    if data.startswith(b"\xff\xd8"):
        pos = 2
        while pos + 4 <= len(data):
            if data[pos] != 0xFF:
                return None
            marker = data[pos + 1]
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                pos += 2
                continue
            length = int.from_bytes(data[pos + 2 : pos + 4], "big")
            if length < 2:
                return None
            if marker in set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}:
                if pos + 9 > len(data):
                    return None
                height = int.from_bytes(data[pos + 5 : pos + 7], "big")
                width = int.from_bytes(data[pos + 7 : pos + 9], "big")
                return ("image/jpeg", width, height)
            pos += 2 + length
        return None
    return None


def image_bounds_error(data: bytes) -> str | None:
    """Bound encoded/decoded image size by header; returns an error or None."""
    if len(data) > MAX_IMAGE_BYTES:
        return f"encoded size {len(data)} exceeds {MAX_IMAGE_BYTES} bytes"
    probed = probe_image(data)
    if probed is None:
        return "unrecognized or truncated PNG/JPEG/WebP image"
    _mime, width, height = probed
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        return f"decoded dimensions {width}x{height} exceed {MAX_IMAGE_PIXELS} pixels"
    return None


def _load_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: unreadable JSON ({exc.__class__.__name__})")
        return None
    if not isinstance(data, dict):
        errors.append(f"{path}: top level must be an object")
        return None
    return data


def validate_index(root: Path, index: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if index.get("schema_version") != SCHEMA_VERSION:
        errors.append("evidence index: schema_version must be 1")
    revision = index.get("product_runtime_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        errors.append("evidence index: product_runtime_revision must be a 40-char hex revision")
    release_ready = index.get("release_ready")
    if not isinstance(release_ready, bool):
        errors.append("evidence index: release_ready must be a boolean")
    gates = index.get("missing_gates")
    if not isinstance(gates, list):
        errors.append("evidence index: missing_gates must be a list")
        gates = []
    gate_ids: list[str] = []
    for gate in gates:
        if not isinstance(gate, dict) or set(gate) != {"id", "status", "detail"}:
            errors.append(
                "evidence index: each missing gate must be an object {id, status, detail}"
            )
            continue
        gate_id, gate_status, gate_detail = gate.get("id"), gate.get("status"), gate.get("detail")
        if not isinstance(gate_id, str) or not gate_id:
            errors.append("evidence index: missing gate id must be a non-empty string")
            continue
        gate_ids.append(gate_id)
        if gate_status not in GATE_STATUSES:
            errors.append(f"evidence index: gate {gate_id}: unknown status {gate_status!r}")
        if not isinstance(gate_detail, str) or not gate_detail:
            errors.append(f"evidence index: gate {gate_id}: detail must be a non-empty string")
    if sorted(gate_ids) != sorted(CANONICAL_GATE_IDS):
        errors.append(
            "evidence index: missing_gates must be exactly the 10 canonical gate IDs; "
            f"got {len(gate_ids)}"
        )
    if len(gate_ids) != len(set(gate_ids)):
        errors.append("evidence index: duplicate missing gate IDs")
    open_gates = [g for g in gates if isinstance(g, dict) and g.get("status") in OPEN_GATE_STATUSES]
    if release_ready is True and open_gates:
        errors.append(
            "evidence index: release_ready=true with open missing gates (false readiness)"
        )
    if release_ready is False and not open_gates:
        errors.append("evidence index: release_ready=false requires explicit missing gates")
    stable_gate_open = any(
        isinstance(g, dict) and g.get("id") == "real-stable-failure" and g.get("status") != "passed"
        for g in gates
    )
    rows = index.get("acceptance")
    if not isinstance(rows, list):
        errors.append("evidence index: acceptance must be a list")
        return errors
    seen: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            errors.append("evidence index: acceptance row must be an object")
            continue
        row_id = str(row.get("id", ""))
        seen.append(row_id)
        status = row.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{row_id}: status {status!r} not in {sorted(ALLOWED_STATUSES)}")
        if stable_gate_open and row_id in STABLE_FAILURE_IDS and status == "live_verified":
            errors.append(f"{row_id}: live_verified contradicts the open real-stable-failure gate")
        row_revision = row.get("run_revision")
        if row_revision == "phase10_delta":
            if not isinstance(row.get("revision_note"), str) or not row["revision_note"].strip():
                errors.append(f"{row_id}: phase10_delta requires an explicit revision_note")
        elif row_revision != revision:
            errors.append(f"{row_id}: run_revision must match the runtime or phase10_delta")
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{row_id}: evidence must be a non-empty list")
            continue
        for ref in evidence:
            if not isinstance(ref, str):
                errors.append(f"{row_id}: evidence ref must be a string")
                continue
            target = safe_path(root, ref)
            if target is None:
                errors.append(f"{row_id}: unsafe evidence path {ref!r}")
            elif not target.is_file():
                errors.append(f"{row_id}: evidence file missing: {ref}")
        test = row.get("test")
        if isinstance(test, str) and "/" in test:
            target = safe_path(root, test.split("::", 1)[0])
            if target is None:
                errors.append(f"{row_id}: unsafe test path {test!r}")
            elif not target.is_file():
                errors.append(f"{row_id}: test file missing: {test}")
    if sorted(seen) != sorted(canonical_ids()):
        errors.append(
            f"evidence index: acceptance IDs must be exactly the 32 canonical IDs; got {len(seen)}"
        )
    if len(seen) != len(set(seen)):
        errors.append("evidence index: duplicate acceptance IDs")
    if release_ready is True and any(
        isinstance(r, dict) and r.get("status") in OPEN_ACCEPTANCE_STATUSES for r in rows
    ):
        errors.append(
            "evidence index: release_ready=true with pending/partial/unmet acceptance rows "
            "(false readiness)"
        )
    return errors


def validate_submission(root: Path, path: Path, index: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [f"submission: missing file {path}"]
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        match = _ID_RE.match(line.strip())
        if match:
            if match.group(1) in parsed:
                errors.append(f"submission: duplicate row {match.group(1)}")
            parsed[match.group(1)] = match.group(2)
    if sorted(parsed) != sorted(canonical_ids()):
        errors.append("submission: table must contain each of the 32 canonical IDs exactly once")
    by_id = {
        str(r.get("id")): str(r.get("status"))
        for r in index.get("acceptance", [])
        if isinstance(r, dict)
    }
    for row_id, status in parsed.items():
        if status not in ALLOWED_STATUSES:
            errors.append(f"submission {row_id}: unknown status {status!r}")
        if by_id.get(row_id) not in (None, status):
            errors.append(
                f"submission {row_id}: status {status} != evidence index {by_id.get(row_id)}"
            )
    readiness = re.findall(r"\brelease_ready\s*[:=]\s*[`*\"']*\s*(true|false)\b", text, re.I)
    expected = str(index.get("release_ready")).lower()
    if not readiness:
        errors.append("submission: must state release_ready true or false explicitly")
    elif any(value.lower() != expected for value in readiness):
        errors.append("submission: release_ready contradicts the evidence index (false readiness)")
    return errors


def validate_links(root: Path, files: list[Path]) -> list[str]:
    errors: list[str] = []
    for md_file in files:
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            errors.append(f"links: missing markdown file {md_file}")
            continue
        base = md_file.parent
        for raw in _LINK_RE.findall(text):
            if raw.startswith(("http://", "https://", "mailto:", "#", "data:")):
                continue  # External URLs are intentionally not verified offline.
            target_ref = raw.split("#", 1)[0]
            if not target_ref:
                continue
            resolved = (base / target_ref).resolve()
            if not resolved.is_relative_to(root.resolve()):
                errors.append(f"{md_file}: link escapes repository: {raw}")
            elif not resolved.exists():
                errors.append(f"{md_file}: broken relative link: {raw}")
    return errors


def validate_sample(root: Path, index: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    sample = index.get("sample_manifest")
    if not isinstance(sample, dict):
        return ["sample manifest: missing sample_manifest object"]
    manifest_rel, source_rel = sample.get("manifest"), sample.get("source_file")
    if not isinstance(manifest_rel, str) or not isinstance(source_rel, str):
        return ["sample manifest: manifest/source_file must be strings"]
    manifest_path, source_path = safe_path(root, manifest_rel), safe_path(root, source_rel)
    if manifest_path is None or source_path is None:
        return ["sample manifest: unsafe manifest/source path"]
    manifest = _load_json(manifest_path, errors)
    if manifest is None or not source_path.is_file():
        return [*errors, "sample manifest: source file missing"]
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    for field in ("original_sha256", "normalized_sha256"):
        if manifest.get(field) != digest:
            errors.append(f"sample manifest: {field} does not match actual file hash")
    if sample.get("recorded_sha256") != digest:
        errors.append("sample manifest: evidence-index recorded_sha256 does not match file hash")
    if sample.get("source_count") != 1:
        errors.append("sample manifest: source_count must be exactly 1")
    return errors


def validate_scenes(root: Path, path: Path) -> list[str]:
    if not path.is_file():
        return ["demo scenes: file missing"]
    errors: list[str] = []
    data = _load_json(path, errors)
    if data is None:
        return errors
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return ["demo scenes: scenes must be a non-empty list"]
    total = 0
    expected_start = 0
    seen_ids: set[str] = set()
    for scene in scenes:
        if not isinstance(scene, dict):
            errors.append("demo scenes: scene must be an object")
            continue
        scene_id = scene.get("id")
        if (
            not isinstance(scene_id, str)
            or not SCENE_ID_RE.fullmatch(scene_id)
            or scene_id in seen_ids
        ):
            errors.append(f"demo scenes: scene id must be a unique safe slug: {scene_id!r}")
            continue
        seen_ids.add(scene_id)
        duration = scene.get("duration_seconds")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
            errors.append(f"demo scenes: scene {scene_id} duration must be a positive integer")
            continue
        start = scene.get("start_seconds")
        if isinstance(start, bool) or not isinstance(start, int) or start != expected_start:
            errors.append(
                f"demo scenes: scene {scene_id} start_seconds must be contiguous ({expected_start})"
            )
        expected_start += duration
        total += duration
        image = scene.get("image")
        if isinstance(image, str) and image:
            target = safe_path(root, image)
            if target is None:
                errors.append(f"demo scenes: unsafe image path {image!r}")
            elif not target.is_file():
                errors.append(f"demo scenes: scene {scene_id} image missing: {image}")
            else:
                payload = target.read_bytes()
                bounds = image_bounds_error(payload)
                if bounds is not None:
                    errors.append(f"demo scenes: scene {scene_id} image {bounds}")
                if scene.get("sha256") != hashlib.sha256(payload).hexdigest():
                    errors.append(f"demo scenes: scene {scene_id} sha256 missing or mismatched")
    if total != 180:
        errors.append(f"demo scenes: durations must sum to exactly 180s, got {total:g}")
    return errors


def inventory(root: Path, index: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in index.get("acceptance", []):
        if isinstance(row, dict):
            counts[str(row.get("status"))] = counts.get(str(row.get("status")), 0) + 1
    return {
        "product_runtime_revision": index.get("product_runtime_revision"),
        "release_ready": index.get("release_ready"),
        "acceptance_rows": sum(counts.values()),
        "status_counts": counts,
        "open_missing_gates": [
            g.get("id")
            for g in index.get("missing_gates", [])
            if isinstance(g, dict) and g.get("status") != "passed"
        ],
        "note": "Structural inventory only; not release readiness. External links/scanners not verified.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--index", default="docs/releases/evidence-index.json")
    parser.add_argument("--submission", default="docs/submission.md")
    parser.add_argument("--scenes", default="docs/demo/scenes.example.json")
    parser.add_argument("--inventory", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    index_path = safe_path(root, args.index)
    if index_path is None:
        print("error: unsafe --index path", file=sys.stderr)
        return 2
    errors: list[str] = []
    index = _load_json(index_path, errors)
    if index is None:
        for error in errors:
            print(f"ERROR {error}")
        return 1
    errors += validate_index(root, index)
    submission_path = safe_path(root, args.submission)
    if submission_path is None:
        errors.append("unsafe --submission path")
    else:
        errors += validate_submission(root, submission_path, index)
    docs = [
        root / name
        for name in (
            "README.md",
            "ATTRIBUTION.md",
            "docs/api.md",
            "docs/submission.md",
            "docs/README.md",
            "docs/demo/storyboard.md",
            "docs/deployment/runbook.md",
        )
    ]
    errors += validate_links(root, [p for p in docs if p.is_file()])
    errors += validate_sample(root, index)
    scenes_path = safe_path(root, args.scenes)
    if scenes_path is not None:
        errors += validate_scenes(root, scenes_path)
    else:
        errors.append("unsafe --scenes path")
    if args.inventory:
        print(json.dumps(inventory(root, index), indent=2))
    for error in errors:
        print(f"ERROR {error}")
    print(
        f"release_checks: {len(errors)} error(s); release_ready={index.get('release_ready')}; "
        "structural validation only, not release readiness; external links/scanners not verified."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
