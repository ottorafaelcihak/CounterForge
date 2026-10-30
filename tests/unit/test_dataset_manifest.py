"""Human admission and byte/split freeze, never generator-provided ground truth."""

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image

from evaluations.dataset import DatasetManifest, freeze, validate_dataset


def dataset(tmp_path):
    entries = []
    for i in range(12):
        out = BytesIO()
        Image.new("RGB", (32, 32), (i * 19, 11, 25)).save(out, format="PNG")
        body = out.getvalue()
        path = f"seed-{i}.png"
        (tmp_path / path).write_bytes(body)
        entries.append(
            dict(
                case_id=f"case-{i}",
                split="development" if i < 8 else "heldout",
                source_path=path,
                normalized_path=path,
                media_type="image/png",
                source_sha256=sha256(body).hexdigest(),
                normalized_sha256=sha256(body).hexdigest(),
                width=32,
                height=32,
                verified_count=i + 1,
                rights=dict(
                    source="Original test image",
                    permission="test-only",
                    redistribution_cleared=True,
                    reviewer="test-human",
                    reviewed_at=datetime.now(UTC).isoformat(),
                ),
                label_reviewer="independent-test-human",
                label_reviewed_at=datetime.now(UTC).isoformat(),
                ambiguity="clear",
                ambiguity_notes="No occlusion; synthetic unit-test approval only.",
            )
        )
    return dict(
        schema_version="1.0",
        revision="test-v1",
        contract_version="1.0.0",
        policy_revision="strict-binary-human-gates-1",
        entries=entries,
        exclusions=[
            dict(case_id="rejected-0", reason="Ambiguous visibility", source_sha256="f" * 64)
        ],
    )


def test_freeze_checks_all_bytes_and_preserves_exclusions(tmp_path):
    m = freeze(dataset(tmp_path), tmp_path)
    assert len(validate_dataset(m, tmp_path).entries) == 12
    assert m.exclusions[0].reason == "Ambiguous visibility"
    changed = m.model_dump(mode="json")
    changed["exclusions"][0]["reason"] = "hidden"
    with pytest.raises(ValueError, match="integrity"):
        validate_dataset(DatasetManifest.model_validate(changed), tmp_path)
    (tmp_path / "seed-0.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash"):
        validate_dataset(m, tmp_path)


@pytest.mark.parametrize(
    "field", ["rights", "label_reviewer", "label_reviewed_at", "verified_count", "ambiguity_notes"]
)
def test_missing_human_evidence_rejected(tmp_path, field):
    data = dataset(tmp_path)
    del data["entries"][0][field]
    with pytest.raises(ValueError):
        freeze(data, tmp_path)


@pytest.mark.parametrize(
    "change",
    ["case", "bytes", "split", "count", "ambiguity", "naive", "rights", "normalized", "pending"],
)
def test_bad_membership_labels_and_normalization(tmp_path, change):
    data = dataset(tmp_path)
    a, b = data["entries"][:2]
    if change == "case":
        b["case_id"] = a["case_id"].upper()
    if change == "bytes":
        b.update(
            {
                k: a[k]
                for k in ("source_path", "normalized_path", "source_sha256", "normalized_sha256")
            }
        )
    if change == "split":
        a["split"] = "heldout"
    if change == "count":
        a["verified_count"] = 17
    if change == "ambiguity":
        a["ambiguity"] = "ambiguous"
    if change == "naive":
        a["label_reviewed_at"] = "2026-10-29T12:00:00"
    if change == "rights":
        a["rights"]["redistribution_cleared"] = False
    if change == "normalized":
        a["normalized_sha256"] = "0" * 64
    if change == "pending":
        a["label_reviewer"] = "PENDING"
    with pytest.raises(ValueError):
        freeze(deepcopy(data), tmp_path)
