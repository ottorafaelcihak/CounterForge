"""No-money PostgreSQL + referenced-artifact backup/restore operator CLI.

Backup is a directory: ``database.dump`` (pg_dump custom format), every
DB-referenced immutable artifact under ``artifacts/``, ``snapshot.json``
(counts plus active/pending job and export intent) and ``manifest.json``
(schema ``counterforge-backup/1`` with per-file SHA-256). The manifest hash
covers the canonical manifest without its own hash field.

Passwords are never placed in argv or stdout: pg tools receive only a
minimal environment containing PATH and PGPASSWORD. Restore is
non-destructive: it refuses nonempty artifact destinations and nonempty
target databases before changing anything, and rejects archive traversal,
symlinks, duplicate paths, hash mismatches and unknown manifest schemas.
PostgreSQL dumps are trusted operator data, not untrusted uploads.

pg_dump/pg_restore are injectable (``--pg-dump``/``--pg-restore`` or PATH).
The API/worker images install postgresql-client-16; on a host without the
tools the CLI fails safely and the rehearsal supplies task-local wrappers.
No provider calls are made by any subcommand.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from services.api.app.db.tables import Tables

SCHEMA = "counterforge-backup/1"
# Reuse the canonical table inventory without reflecting or opening a DB.

TABLES = tuple(field.name for field in fields(Tables) if field.name != "metadata") + (
    "alembic_version",
)


class OpsError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def manifest_digest(manifest: dict[str, Any]) -> str:
    unsigned = {key: val for key, val in manifest.items() if key != "manifest_sha256"}
    return hashlib.sha256(canonical(unsigned)).hexdigest()


def safe_relative(raw: str) -> PurePosixPath:
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or "\\" in raw
        or any(part in {"", ".", ".."} for part in path.parts)
        or str(path) != raw
    ):
        raise OpsError(f"Unsafe backup path rejected: {raw!r}")
    return path


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def safe_existing_file(root: Path, relative: PurePosixPath) -> Path:
    """Resolve a backup-relative file, rejecting symlink parents or escapes."""
    root = Path(root)
    if root.is_symlink():
        raise OpsError("Root directory is a symlink")
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise OpsError(f"Backup file is missing: {relative}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise OpsError(f"Backup path contains a symlink: {relative}")
        if part != relative.parts[-1] and not stat.S_ISDIR(info.st_mode):
            raise OpsError(f"Backup path parent is not a directory: {relative}")
        if part == relative.parts[-1] and not stat.S_ISREG(info.st_mode):
            raise OpsError(f"Backup file is not a regular file: {relative}")
    resolved_root = root.resolve()
    if not current.resolve().is_relative_to(resolved_root):
        raise OpsError(f"Backup path resolves outside its root: {relative}")
    return current


def validate_backup(backup_dir: Path) -> dict[str, Any]:
    """Fully verify a backup directory before anything is changed."""
    root = Path(backup_dir)
    if root.is_symlink() or not root.is_dir():
        raise OpsError("Backup directory is missing or is a symlink")
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise OpsError("Backup manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise OpsError("Backup manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise OpsError("Unrecognized backup manifest schema")
    if manifest.get("manifest_sha256") != manifest_digest(manifest):
        raise OpsError("Backup manifest hash mismatch")
    counts = manifest.get("table_counts")
    if manifest.get("database_schema") != "public" or not isinstance(counts, dict):
        raise OpsError("Backup must cover the dedicated public CounterForge schema")
    if set(counts) != set(TABLES) or any(type(n) is not int or n < 0 for n in counts.values()):
        raise OpsError("Backup table coverage is incomplete or invalid")
    if type(manifest.get("artifact_count")) is not int or manifest["artifact_count"] < 0:
        raise OpsError("Backup blob count is invalid")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise OpsError("Backup manifest has no file entries")
    seen: set[str] = set()
    dump_seen = False
    for entry in files:
        if not isinstance(entry, dict):
            raise OpsError("Invalid backup file entry")
        raw = entry.get("path")
        if not isinstance(raw, str):
            raise OpsError("Invalid backup file entry")
        safe_relative(raw)
        if raw in seen:
            raise OpsError(f"Duplicate backup path rejected: {raw}")
        seen.add(raw)
        target = safe_existing_file(root, PurePosixPath(raw))
        digest, size = hash_file(target)
        if digest != entry.get("sha256") or size != entry.get("byte_size"):
            raise OpsError(f"Backup file hash/size mismatch: {raw}")
        dump_seen = dump_seen or raw == "database.dump"
    if not dump_seen:
        raise OpsError("Backup has no database.dump entry")
    if "snapshot.json" not in seen:
        raise OpsError("Backup has no snapshot.json entry")
    if sum(path.startswith("artifacts/") for path in seen) != manifest["artifact_count"]:
        raise OpsError("Backup blob count differs from manifest")
    snapshot = json.loads((root / "snapshot.json").read_bytes())
    if any(
        snapshot.get(key) != manifest[key]
        for key in ("table_counts", "artifact_count", "database_schema")
    ):
        raise OpsError("Backup snapshot coverage differs from manifest")
    return manifest


def _minimal_env(password: str | None) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if password:
        env["PGPASSWORD"] = password
    return env


def _require_tool(name: str, explicit: str | None) -> str:
    resolved = shutil.which(explicit or name)
    if resolved is None:
        raise OpsError(
            f"{name} not found on PATH; install postgresql-client-16 in the "
            "service image or pass an explicit task-local wrapper executable."
        )
    return resolved


def _run(argv: list[str], env: dict[str, str]) -> None:
    result = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # Never echo stderr wholesale: tool output may embed connection data.
        raise OpsError(f"{Path(argv[0]).name} failed with exit code {result.returncode}")


def _db_parts(settings: Any) -> tuple[str, str, str, str, str | None]:
    url = settings.database_url
    if url.query:
        raise OpsError(
            "DSN query parameters are unsupported; SSL/options must not be silently dropped"
        )
    return (
        url.host or "127.0.0.1",
        str(url.port or 5432),
        url.username or "",
        url.database or "",
        url.password,
    )


def _require_supported_schema(connection: Any) -> None:
    from sqlalchemy import text

    relations = connection.execute(
        text(
            "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p','v','m','f') "
            "AND n.nspname NOT IN ('pg_catalog','information_schema') "
            "AND n.nspname NOT LIKE 'pg_toast%'"
        )
    ).all()
    if any(schema != "public" for schema, _ in relations):
        raise OpsError(
            "Unsupported non-public/mixed source; use a dedicated public CounterForge database"
        )
    if {name for _, name in relations} != set(TABLES):
        raise OpsError("Dedicated public CounterForge table coverage is incomplete or unsupported")


def _counts_on(connection: Any) -> dict[str, int]:
    from sqlalchemy import text

    _require_supported_schema(connection)
    return {
        table: int(connection.scalar(text(f'SELECT count(*) FROM public."{table}"')) or 0)
        for table in TABLES
    }


def _counts(engine: Any) -> dict[str, int]:
    with engine.connect() as connection:
        return _counts_on(connection)


def _snapshot_on(connection: Any) -> dict[str, Any]:
    from sqlalchemy import text

    snapshot: dict[str, Any] = {"table_counts": _counts_on(connection), "active_jobs": []}
    if connection.scalar(text("SELECT to_regclass('public.background_jobs') IS NOT NULL")):
        rows = connection.execute(
            text(
                "SELECT id::text, kind, state FROM public.background_jobs "
                "WHERE state IN ('QUEUED','RUNNING','REVIEW_REQUIRED') ORDER BY id"
            )
        ).mappings()
        snapshot["active_jobs"] = [dict(row) for row in rows]
    snapshot["pending_export_jobs"] = [
        job for job in snapshot["active_jobs"] if job["kind"] == "EXPORT"
    ]
    return snapshot


def _referenced_on(connection: Any) -> list[dict[str, Any]]:
    from sqlalchemy import text

    rows = connection.execute(
        text(
            "SELECT storage_key, sha256, byte_size FROM public.artifact_assets ORDER BY storage_key"
        )
    ).mappings()
    return _unique_blobs(rows)


def _unique_blobs(rows: Any) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        ref = dict(row)
        key = ref["storage_key"]
        if key in unique and unique[key] != ref:
            raise OpsError("Artifact aliases disagree on SHA-256 or byte size")
        unique[key] = ref
    return list(unique.values())


def _copy_verified(
    source: Path,
    destination: Path,
    expected_sha: str,
    expected_size: int,
    *,
    source_root: Path | None = None,
) -> None:
    if source_root is not None:
        try:
            relative = source.relative_to(source_root)
        except ValueError as exc:
            raise OpsError("Artifact source is outside its root") from exc
        safe_existing_file(source_root, PurePosixPath(relative.as_posix()))
    info = source.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OpsError(f"Referenced artifact is not a regular file: {source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(".tmp-" + uuid4().hex)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as src, temporary.open("xb") as dst:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    if digest.hexdigest() != expected_sha or size != expected_size:
        temporary.unlink(missing_ok=True)
        raise OpsError(f"Artifact hash/size mismatch while copying: {source.name}")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)


def do_backup(settings: Any, output_dir: Path, pg_dump_bin: str | None) -> Path:
    from services.api.app.db.uow import create_database_engine

    pg_dump = _require_tool("pg_dump", pg_dump_bin)
    host, port, user, database, password = _db_parts(settings)
    engine = create_database_engine(settings)
    try:
        from sqlalchemy import text

        output_dir = Path(os.path.abspath(output_dir))
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        final = output_dir / f"counterforge-backup-{stamp}"
        if final.exists():
            raise OpsError(f"Backup destination already exists: {final.name}")
        staging = output_dir / (".tmp-" + final.name + "-" + uuid4().hex)
        staging.mkdir(mode=0o700)
        try:
            dump_path = staging / "database.dump"
            # One exported REPEATABLE READ snapshot drives both the
            # reference/count queries and pg_dump --snapshot, so a commit
            # during backup cannot omit a referenced blob or skew counts.
            with engine.connect() as connection:
                connection = connection.execution_options(isolation_level="REPEATABLE READ")
                transaction = connection.begin()
                try:
                    exported = connection.scalar(text("SELECT pg_export_snapshot()"))
                    _require_supported_schema(connection)
                    references = _referenced_on(connection)
                    snapshot = _snapshot_on(connection)
                    _run(
                        [
                            pg_dump,
                            f"--host={host}",
                            f"--port={port}",
                            f"--username={user}",
                            f"--dbname={database}",
                            "--format=custom",
                            "--no-owner",
                            f"--snapshot={exported}",
                            f"--file={dump_path}",
                        ],
                        _minimal_env(password),
                    )
                finally:
                    transaction.rollback()
            os.chmod(dump_path, 0o600)
            entries: list[dict[str, Any]] = []
            dump_sha, dump_size = hash_file(dump_path)
            entries.append({"path": "database.dump", "sha256": dump_sha, "byte_size": dump_size})
            artifact_root = Path(os.path.abspath(settings.artifact_root))
            for ref in references:
                relative = safe_relative(ref["storage_key"])
                _copy_verified(
                    artifact_root.joinpath(*relative.parts),
                    staging.joinpath("artifacts", *relative.parts),
                    ref["sha256"],
                    int(ref["byte_size"]),
                    source_root=artifact_root,
                )
                entries.append(
                    {
                        "path": f"artifacts/{ref['storage_key']}",
                        "sha256": ref["sha256"],
                        "byte_size": int(ref["byte_size"]),
                    }
                )
            snapshot_doc = {
                "schema": SCHEMA,
                "created_at": datetime.now(UTC).isoformat(),
                "database_schema": "public",
                "table_counts": snapshot["table_counts"],
                "active_jobs": snapshot["active_jobs"],
                "pending_export_jobs": snapshot["pending_export_jobs"],
                "artifact_count": len(references),
            }
            (staging / "snapshot.json").write_bytes(canonical(snapshot_doc) + b"\n")
            os.chmod(staging / "snapshot.json", 0o600)
            snap_sha, snap_size = hash_file(staging / "snapshot.json")
            entries.append({"path": "snapshot.json", "sha256": snap_sha, "byte_size": snap_size})
            manifest: dict[str, Any] = {
                "schema": SCHEMA,
                "created_at": snapshot_doc["created_at"],
                "files": sorted(entries, key=lambda e: e["path"]),
                "database_schema": "public",
                "table_counts": snapshot["table_counts"],
                "artifact_count": len(references),
            }
            manifest["manifest_sha256"] = manifest_digest(manifest)
            (staging / "manifest.json").write_bytes(canonical(manifest) + b"\n")
            os.chmod(staging / "manifest.json", 0o600)
            validate_backup(staging)
            os.replace(staging, final)
            dir_fd = os.open(output_dir, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return final
    finally:
        engine.dispose()


def do_restore(
    settings: Any,
    backup_dir: Path,
    artifact_root: Path,
    pg_restore_bin: str | None,
    *,
    confirm_disposable: bool,
) -> dict[str, Any]:
    from sqlalchemy import text

    from services.api.app.db.uow import create_database_engine

    if not confirm_disposable:
        raise OpsError("Restore requires --confirm-disposable-restore")
    manifest = validate_backup(backup_dir)  # all checks before any change
    artifact_root = Path(os.path.abspath(artifact_root))
    if artifact_root.is_symlink():
        raise OpsError("Artifact destination must not be a symlink")
    if artifact_root.exists() and any(artifact_root.iterdir()):
        raise OpsError("Artifact destination must be empty or not yet exist")
    pg_restore = _require_tool("pg_restore", pg_restore_bin)
    host, port, user, database, password = _db_parts(settings)
    engine = create_database_engine(settings)
    try:
        with engine.connect() as connection:
            relations = int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                        "ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p','v','m','f') "
                        "AND n.nspname NOT IN ('pg_catalog','information_schema') "
                        "AND n.nspname NOT LIKE 'pg_toast%'"
                    )
                )
                or 0
            )
        if relations:
            raise OpsError("Target database is not empty; use a disposable restored database")
        _run(
            [
                pg_restore,
                f"--host={host}",
                f"--port={port}",
                f"--username={user}",
                f"--dbname={database}",
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                "--single-transaction",
                str(Path(backup_dir) / "database.dump"),
            ],
            _minimal_env(password),
        )
        artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        restored = 0
        for entry in manifest["files"]:
            if not entry["path"].startswith("artifacts/"):
                continue
            relative = safe_relative(entry["path"][len("artifacts/") :])
            _copy_verified(
                Path(backup_dir).joinpath(*PurePosixPath(entry["path"]).parts),
                artifact_root.joinpath(*relative.parts),
                entry["sha256"],
                int(entry["byte_size"]),
                source_root=Path(backup_dir),
            )
            restored += 1
        counts = _counts(engine)
        expected = manifest.get("table_counts") or {}
        mismatched = {
            name: (expected.get(name), counts.get(name))
            for name in expected
            if counts.get(name) != expected[name]
        }
        if mismatched:
            raise OpsError(f"Restored table counts differ: {sorted(mismatched)}")
        with engine.connect() as connection:
            references = _referenced_on(connection)
        if restored != manifest["artifact_count"] or len(references) != restored:
            raise OpsError("Restored unique blob count differs from manifest")
        return {"restored_artifacts": restored, "table_counts": counts}
    finally:
        engine.dispose()


def prune_backups(backup_root: Path, retention_days: int, *, delete: bool) -> list[str]:
    if retention_days < 1:
        raise OpsError("Retention must be at least one day")
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    selected: list[str] = []
    root = Path(backup_root)
    if not root.is_dir():
        return selected
    for candidate in sorted(root.glob("counterforge-backup-*")):
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        try:
            manifest = validate_backup(candidate)
            created = datetime.fromisoformat(str(manifest["created_at"]).replace("Z", "+00:00"))
        except (OpsError, ValueError, KeyError):
            continue  # never delete an unverifiable directory
        if created >= cutoff:
            continue
        selected.append(candidate.name)
        if delete:
            shutil.rmtree(candidate)
    return selected


def _settings() -> Any:
    from services.api.app.config import Settings

    return Settings.from_env()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup", help="dump PostgreSQL + referenced artifacts")
    backup.add_argument("--output-dir", type=Path, required=True)
    backup.add_argument("--pg-dump", default=None)
    restore = sub.add_parser("restore", help="restore into an empty disposable destination")
    restore.add_argument("--backup-dir", type=Path, required=True)
    restore.add_argument("--artifact-root", type=Path, required=True)
    restore.add_argument("--pg-restore", default=None)
    restore.add_argument("--confirm-disposable-restore", action="store_true")
    verify = sub.add_parser("verify", help="verify manifest and file hashes offline")
    verify.add_argument("--backup-dir", type=Path, required=True)
    prune = sub.add_parser("prune-backups", help="retention for backup dirs (dry-run default)")
    prune.add_argument("--backup-root", type=Path, required=True)
    prune.add_argument("--retention-days", type=int, default=30)
    prune.add_argument("--delete", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            manifest = validate_backup(args.backup_dir)
            print(
                f"verified {len(manifest['files'])} files, "
                f"{manifest.get('artifact_count', 0)} artifacts, schema {SCHEMA}"
            )
        elif args.command == "prune-backups":
            names = prune_backups(args.backup_root, args.retention_days, delete=args.delete)
            for name in names:
                print(("deleted " if args.delete else "would delete ") + name)
            print(f"{'Deleted' if args.delete else 'Dry run:'} {len(names)} expired backups.")
        elif args.command == "backup":
            final = do_backup(_settings(), args.output_dir, args.pg_dump)
            print(f"backup written: {final.name}")
        else:
            result = do_restore(
                _settings(),
                args.backup_dir,
                args.artifact_root,
                args.pg_restore,
                confirm_disposable=args.confirm_disposable_restore,
            )
            print(
                f"restore complete: {result['restored_artifacts']} artifacts, "
                f"tables={result['table_counts']}"
            )
    except (OpsError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
