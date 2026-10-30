"""Ops CLI guardrails: backup integrity, safe paths, retention, latency math."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.backup_restore import (
    SCHEMA,
    TABLES,
    OpsError,
    _db_parts,
    _require_supported_schema,
    _unique_blobs,
    canonical,
    manifest_digest,
    prune_backups,
    safe_relative,
    validate_backup,
)
from scripts.measure_latency import percentile, summarize


def make_backup(root: Path, *, schema: str = SCHEMA) -> Path:
    backup = root / "counterforge-backup-20260101T000000Z"
    backup.mkdir(parents=True)
    dump = b"PGDMP-fixture"
    (backup / "database.dump").write_bytes(dump)
    table_counts = {name: 0 for name in TABLES}
    table_counts["artifact_assets"] = 2  # two DB aliases, one physical blob
    coverage = {"database_schema": "public", "table_counts": table_counts, "artifact_count": 1}
    snap = canonical({"schema": SCHEMA, **coverage}) + b"\n"
    (backup / "snapshot.json").write_bytes(snap)
    artifact = b"evidence-bytes"
    target = backup / "artifacts/projects/p/sha256/ab/blob.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(artifact)
    manifest = {
        "schema": schema,
        "created_at": datetime.now(UTC).isoformat(),
        **coverage,
        "files": [
            {
                "path": "database.dump",
                "sha256": hashlib.sha256(dump).hexdigest(),
                "byte_size": len(dump),
            },
            {
                "path": "snapshot.json",
                "sha256": hashlib.sha256(snap).hexdigest(),
                "byte_size": len(snap),
            },
            {
                "path": "artifacts/projects/p/sha256/ab/blob.png",
                "sha256": hashlib.sha256(artifact).hexdigest(),
                "byte_size": len(artifact),
            },
        ],
    }
    manifest["manifest_sha256"] = manifest_digest(manifest)
    (backup / "manifest.json").write_bytes(canonical(manifest) + b"\n")
    return backup


def test_validate_backup_accepts_known_bundle(tmp_path):
    backup = make_backup(tmp_path)
    manifest = validate_backup(backup)
    assert manifest["artifact_count"] == 1 and len(manifest["files"]) == 3


def test_validate_backup_rejects_tamper_and_unknown_schema(tmp_path):
    backup = make_backup(tmp_path)
    (backup / "database.dump").write_bytes(b"tampered")
    with pytest.raises(OpsError, match="hash/size mismatch"):
        validate_backup(backup)
    other = make_backup(tmp_path / "second", schema="unknown/9")
    # fix hash so only the schema gate can reject it
    manifest = json.loads((other / "manifest.json").read_bytes())
    manifest["manifest_sha256"] = manifest_digest(manifest)
    (other / "manifest.json").write_bytes(canonical(manifest) + b"\n")
    with pytest.raises(OpsError, match="Unrecognized"):
        validate_backup(other)


def test_validate_backup_rejects_duplicate_traversal_and_symlink(tmp_path):
    with pytest.raises(OpsError):
        safe_relative("../escape")
    with pytest.raises(OpsError):
        safe_relative("/absolute/path")
    with pytest.raises(OpsError):
        safe_relative("a//b")
    backup = make_backup(tmp_path)
    manifest = json.loads((backup / "manifest.json").read_bytes())
    manifest["files"].append(dict(manifest["files"][0]))
    manifest["manifest_sha256"] = manifest_digest(manifest)
    (backup / "manifest.json").write_bytes(canonical(manifest) + b"\n")
    with pytest.raises(OpsError, match="Duplicate"):
        validate_backup(backup)

    linked = make_backup(tmp_path / "linked")
    victim = linked / "database.dump"
    victim.unlink()
    victim.symlink_to("/etc/hostname")
    with pytest.raises(OpsError, match="symlink|regular file"):
        validate_backup(linked)
    # Symlinked parent directory is rejected even when the leaf is real.
    parent_linked = make_backup(tmp_path / "parent-linked")
    real_dir = tmp_path / "elsewhere"
    real_dir.mkdir()
    (real_dir / "blob.png").write_bytes(b"evidence-bytes")
    link_parent = parent_linked / "artifacts/projects/p/sha256/ab"
    import shutil

    shutil.rmtree(link_parent)
    link_parent.symlink_to(real_dir, target_is_directory=True)
    with pytest.raises(OpsError, match="symlink"):
        validate_backup(parent_linked)


def test_prune_backups_is_dry_run_by_default_and_only_deletes_expired(tmp_path):
    old = make_backup(tmp_path)
    manifest = json.loads((old / "manifest.json").read_bytes())
    manifest["created_at"] = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    manifest["manifest_sha256"] = manifest_digest(manifest)
    (old / "manifest.json").write_bytes(canonical(manifest) + b"\n")
    unknown = tmp_path / "counterforge-backup-mystery"
    unknown.mkdir()
    assert prune_backups(tmp_path, 30, delete=False) == [old.name]
    assert old.exists()  # dry run changed nothing
    assert prune_backups(tmp_path, 30, delete=True) == [old.name]
    assert not old.exists() and unknown.exists()  # unverifiable dirs never deleted


def test_latency_base_url_is_loopback_origin_only():
    from scripts.measure_latency import validate_base_url

    assert validate_base_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    for bad in (
        "https://127.0.0.1:8000",
        "http://example.com:8000",
        "http://user@127.0.0.1:8000",
        "http://127.0.0.1:8000/path",
        "http://127.0.0.1:8000/?q=1",
    ):
        with pytest.raises(ValueError):
            validate_base_url(bad)


def test_latency_percentile_and_summary_are_observed_values():
    samples = [float(value) for value in range(1, 101)]
    assert percentile(samples, 0.95) == 95.0
    summary = summarize(samples)
    assert summary["samples"] == 100 and summary["p95_ms"] == 95.0
    assert summary["target_p95_ms"] == 500.0 and summary["p95_within_target"] is True


def test_worker_heartbeat_key_is_exact_and_queue_specific():
    from services.api.app.db.readiness import worker_heartbeat_key

    assert worker_heartbeat_key("counterforge") == "counterforge:worker-heartbeat:counterforge"
    assert worker_heartbeat_key("ops-rehearsal") != worker_heartbeat_key("counterforge")


def test_alias_blobs_deduplicate_but_conflicting_metadata_fails_closed():
    ref = {"storage_key": "projects/p/blob", "sha256": "a" * 64, "byte_size": 12}
    assert _unique_blobs([ref, dict(ref)]) == [ref]
    for changes in ({"sha256": "b" * 64}, {"byte_size": 13}):
        with pytest.raises(OpsError, match="aliases disagree"):
            _unique_blobs([ref, {**ref, **changes}])


@pytest.mark.parametrize(
    "missing",
    [
        "model_observations",
        "mutation_validations",
        "public_demo_snapshots",
        "alembic_version",
        "all",
    ],
)
def test_manifest_requires_full_schema_coverage(tmp_path, missing):
    backup = make_backup(tmp_path)
    path = backup / "manifest.json"
    manifest = json.loads(path.read_bytes())
    if missing == "all":
        manifest["table_counts"] = {}
    else:
        del manifest["table_counts"][missing]
    manifest["manifest_sha256"] = manifest_digest(manifest)
    path.write_bytes(canonical(manifest))
    with pytest.raises(OpsError, match="coverage"):
        validate_backup(backup)


def test_unsupported_database_namespaces_are_rejected_before_backup():
    from types import SimpleNamespace

    for relations in (
        [],
        [("private", name) for name in TABLES],
        [("public", name) for name in TABLES] + [("private", "extra")],
    ):
        connection = SimpleNamespace(
            execute=lambda query, rows=relations: SimpleNamespace(all=lambda: rows)
        )
        with pytest.raises(OpsError, match="source|coverage"):
            _require_supported_schema(connection)
    connection = SimpleNamespace(
        execute=lambda query: SimpleNamespace(all=lambda: [("public", name) for name in TABLES])
    )
    _require_supported_schema(connection)


def test_database_query_settings_are_explicitly_rejected():
    from types import SimpleNamespace

    from sqlalchemy.engine import make_url

    for query in ("sslmode=require", "options=-csearch_path%3Dprivate", "connect_timeout=9"):
        settings = SimpleNamespace(
            database_url=make_url("postgresql+psycopg://user@localhost/db?" + query)
        )
        with pytest.raises(OpsError, match="query parameters are unsupported"):
            _db_parts(settings)
