"""Unit regressions for the offline Phase 10 release checker."""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from scripts.release_checks import (
    CANONICAL_GATE_IDS,
    canonical_ids,
    safe_path,
    validate_index,
    validate_sample,
    validate_scenes,
    validate_submission,
)

REV = "a" * 40


def _index(tmp_path: Path) -> dict[str, Any]:
    evidence = tmp_path / "evidence.md"
    evidence.write_text("evidence\n", encoding="utf-8")
    return {
        "schema_version": 1,
        "product_runtime_revision": REV,
        "release_ready": False,
        "missing_gates": [
            {"id": gate_id, "status": "unmet", "detail": "open"} for gate_id in CANONICAL_GATE_IDS
        ],
        "acceptance": [
            {
                "id": row_id,
                "status": "fixture_verified",
                "evidence": ["evidence.md"],
                "test": "evidence.md",
                "run_revision": REV,
                "date": "2026-10-30",
                "deviation": "none",
            }
            for row_id in canonical_ids()
        ],
    }


def test_valid_index_passes(tmp_path: Path) -> None:
    assert validate_index(tmp_path, _index(tmp_path)) == []


def test_submission_cannot_hide_true_readiness_with_unrelated_false(tmp_path: Path) -> None:
    index = _index(tmp_path)
    text = "`release_ready: true`\nThese are not false positives.\n"
    text += "\n".join(f"| {r['id']} | {r['status']} |" for r in index["acceptance"])
    path = tmp_path / "submission.md"
    path.write_text(text)
    assert any("false readiness" in e for e in validate_submission(tmp_path, path, index))
    path.write_text(text.replace("release_ready: true", "release_ready: false"))
    assert validate_submission(tmp_path, path, index) == []
    path.write_text(text + "\nrelease_ready: false\n")
    assert any("false readiness" in e for e in validate_submission(tmp_path, path, index))


def test_phase10_revision_requires_explicit_note(tmp_path: Path) -> None:
    index = _index(tmp_path)
    index["acceptance"][0]["run_revision"] = "phase10_delta"
    assert any("revision_note" in e for e in validate_index(tmp_path, index))
    index["acceptance"][0]["revision_note"] = "Git commit containing this index; current workspace."
    assert validate_index(tmp_path, index) == []


def test_scenes_require_existing_file_and_bound_hash(tmp_path: Path) -> None:
    path = tmp_path / "scenes.json"
    assert validate_scenes(tmp_path, path) == ["demo scenes: file missing"]
    encoded = BytesIO()
    Image.new("RGB", (4, 4)).save(encoded, format="PNG")
    payload = encoded.getvalue()
    (tmp_path / "still.png").write_bytes(payload)
    row = {"id": "seed", "start_seconds": 0, "duration_seconds": 180, "image": "still.png"}
    path.write_text(json.dumps({"scenes": [row]}))
    assert any("sha256" in e for e in validate_scenes(tmp_path, path))
    row["sha256"] = hashlib.sha256(payload).hexdigest()
    path.write_text(json.dumps({"scenes": [row]}))
    assert validate_scenes(tmp_path, path) == []


def test_missing_gate_required_when_not_ready(tmp_path: Path) -> None:
    index = _index(tmp_path)
    index["missing_gates"] = []
    errors = validate_index(tmp_path, index)
    assert any("missing gates" in e for e in errors)


def test_false_live_claim_rejected(tmp_path: Path) -> None:
    index = _index(tmp_path)
    for row in index["acceptance"]:
        if row["id"] == "TS-AT-07":
            row["status"] = "live_verified"
    errors = validate_index(tmp_path, index)
    assert any("contradicts the open real-stable-failure gate" in e for e in errors)


def test_release_ready_with_open_gates_rejected(tmp_path: Path) -> None:
    index = _index(tmp_path)
    index["release_ready"] = True
    errors = validate_index(tmp_path, index)
    assert any("false readiness" in e for e in errors)


def test_unsafe_paths_rejected(tmp_path: Path) -> None:
    assert safe_path(tmp_path, "../outside.txt") is None
    assert safe_path(tmp_path, "/etc/passwd") is None
    link = tmp_path / "escape.md"
    link.symlink_to("/etc/passwd")
    assert safe_path(tmp_path, "escape.md") is None
    index = _index(tmp_path)
    index["acceptance"][0]["evidence"] = ["../outside.txt"]
    assert any("unsafe evidence path" in e for e in validate_index(tmp_path, index))


def test_tampered_sample_hash_rejected(tmp_path: Path) -> None:
    source = tmp_path / "five-blue-containers.png"
    source.write_bytes(b"png-bytes")
    good = hashlib.sha256(b"png-bytes").hexdigest()
    manifest = {"original_sha256": good, "normalized_sha256": good}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    index: dict[str, Any] = {
        "sample_manifest": {
            "manifest": "manifest.json",
            "source_file": "five-blue-containers.png",
            "recorded_sha256": good,
            "source_count": 1,
        }
    }
    assert validate_sample(tmp_path, index) == []
    source.write_bytes(b"tampered")
    errors = validate_sample(tmp_path, index)
    assert any("does not match" in e for e in errors)


def test_scenes_must_sum_to_180(tmp_path: Path) -> None:
    path = tmp_path / "scenes.json"
    path.write_text(
        json.dumps(
            {"scenes": [{"id": "a", "start_seconds": 0, "duration_seconds": 179, "image": ""}]}
        ),
        encoding="utf-8",
    )
    assert any("exactly 180" in e for e in validate_scenes(tmp_path, path))


def test_missing_gate_id_rejected(tmp_path: Path) -> None:
    index = _index(tmp_path)
    index["missing_gates"] = index["missing_gates"][:-1]
    assert any("10 canonical gate" in e for e in validate_index(tmp_path, index))


def test_empty_gates_rejected(tmp_path: Path) -> None:
    index = _index(tmp_path)
    index["missing_gates"] = []
    errors = validate_index(tmp_path, index)
    assert any("10 canonical gate" in e for e in errors)
    assert any("missing gates" in e for e in errors)


def test_unknown_gate_status_rejected(tmp_path: Path) -> None:
    index = _index(tmp_path)
    index["missing_gates"][0]["status"] = "done-ish"
    assert any("unknown status" in e for e in validate_index(tmp_path, index))


def test_pending_acceptance_blocks_release_ready(tmp_path: Path) -> None:
    index = _index(tmp_path)
    index["release_ready"] = True
    for gate in index["missing_gates"]:
        gate["status"] = "passed"
    index["acceptance"][0]["status"] = "pending"
    errors = validate_index(tmp_path, index)
    assert any("pending/partial/unmet acceptance" in e for e in errors)


def test_in_root_symlink_rejected(tmp_path: Path) -> None:
    target = tmp_path / "real.md"
    target.write_text("x\n", encoding="utf-8")
    (tmp_path / "alias.md").symlink_to(target)
    assert safe_path(tmp_path, "alias.md") is None
    assert safe_path(tmp_path, "real.md") is not None


def test_scenes_reject_duplicate_noncontiguous_fractional(tmp_path: Path) -> None:
    path = tmp_path / "scenes.json"
    path.write_text(
        json.dumps(
            {
                "scenes": [
                    {"id": "a", "start_seconds": 0, "duration_seconds": 90.5, "image": ""},
                    {"id": "a", "start_seconds": 99, "duration_seconds": 90, "image": ""},
                ]
            }
        ),
        encoding="utf-8",
    )
    errors = validate_scenes(tmp_path, path)
    assert any("positive integer" in e for e in errors)
    assert any("unique" in e for e in errors)
