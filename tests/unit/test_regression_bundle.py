"""Portable bundle integrity and hostile retained content must fail closed."""

import io
import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4
from zipfile import ZipFile

import pytest


def example_manifest():
    from counterforge_regression.manifest import RegressionManifest

    return RegressionManifest(
        finding_id=uuid4(),
        export_job_id=uuid4(),
        test_id="fixture-count",
        question="How many objects? Return only an integer.",
        expected={"kind": "integer", "value": 5, "verified_by": "human"},
        model={
            "adapter": "LOCAL_FIXTURE",
            "model_id": "LOCAL_FIXTURE",
            "revision": "fixture-v1",
            "sampling_options": {},
        },
        invariant_verification={
            "verdict": "VALID",
            "reviewed_by": "owner",
            "verifier_version": "test-v1",
        },
        confirmation={"screening_failures": 1, "fresh_attempts": 3, "fresh_failures": 3},
        mutation={"family": "brightness", "strength": 0.1, "recipe_version": "1.0.0"},
        provenance="LOCAL_FIXTURE",
        recorded_at=datetime(2026, 10, 29, tzinfo=UTC),
    )


def evidence():
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG")
    return {
        "evidence/original.png": output.getvalue(),
        "evidence/failing.png": output.getvalue(),
        "evidence/protected-mask.png": output.getvalue(),
        "evidence/comparison.png": output.getvalue(),
        "evidence/validity-report.json": b'{"verdict":"VALID"}',
        "evidence/recipe.json": b'{"family":"brightness"}',
        "evidence/target-responses.json": json.dumps(
            [
                {"phase": "SCREENING", "raw_text": "4", "parsed_count": 4, "outcome": "INCORRECT"},
                *[
                    {
                        "phase": "CONFIRMATION",
                        "raw_text": "4",
                        "parsed_count": 4,
                        "outcome": "INCORRECT",
                    }
                    for _ in range(3)
                ],
            ]
        ).encode(),
    }


def test_bundle_has_all_members_hashes_and_deterministic_metadata():
    from counterforge_regression.bundle import build_bundle, verify_bundle

    manifest = example_manifest()
    bundle = build_bundle(manifest, evidence())
    assert bundle == build_bundle(manifest, evidence())
    verify_bundle(bundle.zip_bytes)
    with ZipFile(io.BytesIO(bundle.zip_bytes)) as archive:
        names = set(archive.namelist())
        assert {
            "README.md",
            "manifest.json",
            "requirements.txt",
            "runner/target_adapter.py",
            "tests/test_visual_regression.py",
            "sha256sums.txt",
        } <= names
        sums = {
            name: digest
            for digest, name in (
                line.split("  ") for line in archive.read("sha256sums.txt").decode().splitlines()
            )
        }
        assert set(sums) == names - {"sha256sums.txt"}
        assert all(
            sha256(archive.read(name)).hexdigest() == digest for name, digest in sums.items()
        )
        public = json.loads(archive.read("manifest.json"))
        assert "manifest.json" not in public["sha256"]
        assert set(public["sha256"]) == names - {"manifest.json", "sha256sums.txt"}
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())


def test_original_format_bundle_without_refinement_metadata_remains_readable_unchanged():
    from counterforge_regression.bundle import build_bundle, canonical, verify_bundle
    from counterforge_regression.manifest import RegressionManifest

    bundle = build_bundle(example_manifest(), evidence())
    with ZipFile(io.BytesIO(bundle.zip_bytes)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    original = json.loads(members["manifest.json"])
    for field in ("parent_mutation_id", "parent_finding_id", "minimization_job_id"):
        original.pop(field)
    members["manifest.json"] = canonical(original)
    members["sha256sums.txt"] = "".join(
        f"{sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(members.items())
        if name != "sha256sums.txt"
    ).encode()
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    retained = output.getvalue()
    verify_bundle(retained)
    parsed = RegressionManifest.model_validate_json(members["manifest.json"])
    assert (
        parsed.parent_mutation_id is parsed.parent_finding_id is parsed.minimization_job_id is None
    )
    assert output.getvalue() == retained


@pytest.mark.parametrize("damage", ["missing", "path", "secret", "binary-secret"])
def test_bundle_rejects_missing_unsafe_or_secret_content(damage):
    from counterforge_regression.bundle import build_bundle

    members = evidence()
    if damage == "missing":
        del members["evidence/failing.png"]
    elif damage == "path":
        members["../escape"] = b"bad"
    elif damage == "secret":
        members["evidence/recipe.json"] = b'{"url":"https://private.example/signed?token=secret"}'
    else:
        members["evidence/original-source.jpg"] = (
            b"\xff\xd8private /home/otto/secret sk-secret1234567890"
        )
    with pytest.raises(ValueError):
        build_bundle(example_manifest(), members)


def test_bundle_rejects_asset_hash_mismatch():
    from counterforge_regression.bundle import build_bundle

    manifest = example_manifest().model_copy(update={"sha256": {"evidence/failing.png": "a" * 64}})
    with pytest.raises(ValueError, match="hash"):
        build_bundle(manifest, evidence())


def test_bundle_verifier_rejects_symlink_zip_member():
    from zipfile import ZipInfo

    from counterforge_regression.bundle import build_bundle, verify_bundle

    bundle = build_bundle(example_manifest(), evidence())
    output = io.BytesIO()
    with ZipFile(io.BytesIO(bundle.zip_bytes)) as source, ZipFile(output, "w") as target:
        for name in source.namelist():
            info = ZipInfo(name)
            if name == "evidence/failing.png":
                info.create_system = 3
                info.external_attr = 0o120777 << 16
            target.writestr(info, source.read(name))
    with pytest.raises(ValueError, match="member"):
        verify_bundle(output.getvalue())


def test_bundle_refuses_unexpected_files_even_with_safe_paths():
    from counterforge_regression.bundle import build_bundle

    files = evidence()
    files["evidence/provider-generated.py"] = b'print("untrusted")'
    with pytest.raises(ValueError, match="member"):
        build_bundle(example_manifest(), files)


def test_compressed_png_metadata_secrets_cannot_hide_in_original_source():
    from counterforge_regression.bundle import build_bundle
    from PIL import Image, PngImagePlugin

    output = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "https://private.example/signed?token=secret", zip=True)
    Image.new("RGB", (2, 2), "white").save(output, format="PNG", pnginfo=metadata)
    files = evidence()
    files["evidence/original-source.png"] = output.getvalue()
    with pytest.raises(ValueError, match="Unsafe"):
        build_bundle(example_manifest(), files)


def test_known_credentials_are_rejected_even_without_a_secret_prefix():
    from counterforge_regression.bundle import build_bundle

    members = evidence()
    members["evidence/recipe.json"] = b'{"note":"opaqueCredentialWithoutKnownPrefix12345"}'
    with pytest.raises(ValueError, match="secret"):
        build_bundle(
            example_manifest(),
            members,
            forbidden_values=(b"opaqueCredentialWithoutKnownPrefix12345",),
        )
