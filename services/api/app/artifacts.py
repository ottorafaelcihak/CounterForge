"""Immutable, verified, content-addressed local evidence storage."""

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from .db.uow import UnitOfWork

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ASSET_BYTES = 100 * 1024 * 1024
POLICY = "exif-rgb-png-strip-metadata-v1"
ARTIFACT_REGISTRATION_LOCK = 0x43464F524745
EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "application/json": "json",
    "application/zip": "zip",
    "text/plain": "txt",
    "application/octet-stream": "bin",
}


class InvalidImage(ValueError):
    pass


class IntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedImage:
    png: bytes
    width: int
    height: int
    original_sha256: str
    normalized_sha256: str
    pixel_sha256: str
    original_width: int
    original_height: int
    policy: str = POLICY


def normalize_image(
    data: bytes,
    media_type: str,
    *,
    strict_opaque: bool = False,
    expected_size: tuple[int, int] | None = None,
) -> NormalizedImage:
    if (
        not data
        or len(data) > MAX_UPLOAD_BYTES
        or media_type not in {"image/png", "image/jpeg", "image/webp"}
    ):
        raise InvalidImage("Unsupported or oversized image")
    try:
        process = subprocess.run(
            [
                sys.executable,
                "-I",
                str(Path(__file__).with_name("_image_decode.py")),
                media_type,
                "strict" if strict_opaque else "seed",
            ],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            env={},
            check=True,
        )
        header, png = process.stdout.split(b"\n", 1)
        info = json.loads(header)
        if expected_size is not None and (info["width"], info["height"]) != expected_size:
            raise ValueError
        if not png or len(png) > 64 * 1024 * 1024:
            raise ValueError
        return NormalizedImage(
            png=png,
            width=info["width"],
            height=info["height"],
            original_sha256=hashlib.sha256(data).hexdigest(),
            normalized_sha256=hashlib.sha256(png).hexdigest(),
            pixel_sha256=info["pixel_sha256"],
            original_width=info["original_width"],
            original_height=info["original_height"],
        )
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        raise InvalidImage("Image could not be safely decoded") from None


@dataclass(frozen=True)
class BlobRef:
    project_id: UUID
    storage_key: str
    sha256: str
    byte_size: int
    media_type: str


@dataclass(frozen=True)
class ArtifactRef(BlobRef):
    id: UUID
    kind: str


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.abspath(root))

    @staticmethod
    def key(project_id: UUID, digest: str, media_type: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or media_type not in EXTENSIONS:
            raise IntegrityError("Invalid artifact reference")
        return f"projects/{project_id}/sha256/{digest[:2]}/{digest}.{EXTENSIONS[media_type]}"

    @contextmanager
    def _directory(self, parts: tuple[str, ...], create: bool = False) -> Iterator[int]:
        # Walk from / using directory descriptors, rejecting every symlink ancestor.
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in (*self.root.parts[1:], *parts):
                if part in {"", ".", ".."} or "/" in part:
                    raise IntegrityError("Invalid artifact path")
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                following = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
                os.close(descriptor)
                descriptor = following
            yield descriptor
        except OSError:
            raise IntegrityError("Artifact access failed") from None
        finally:
            os.close(descriptor)

    def put(self, project_id: UUID, data: bytes, media_type: str) -> BlobRef:
        if not 1 <= len(data) <= MAX_ASSET_BYTES:
            raise IntegrityError("Invalid artifact size")
        digest = hashlib.sha256(data).hexdigest()
        key = self.key(project_id, digest, media_type)
        ref = BlobRef(project_id, key, digest, len(data), media_type)
        parts = Path(key).parts
        with self._directory(parts[:-1], create=True) as directory:
            try:
                existing = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                if not stat.S_ISREG(existing.st_mode):
                    raise IntegrityError("Artifact path is not a regular file")
                self.read_verified(ref)
                return ref
            temporary = ".tmp-" + uuid4().hex
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                with os.fdopen(descriptor, "wb") as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, parts[-1], src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            except OSError:
                raise IntegrityError("Atomic artifact write failed") from None
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
        return ref

    def read_verified(self, ref: BlobRef) -> bytes:
        if ref.storage_key != self.key(ref.project_id, ref.sha256, ref.media_type):
            raise IntegrityError("Invalid artifact reference")
        if not 1 <= ref.byte_size <= MAX_ASSET_BYTES:
            raise IntegrityError("Invalid artifact size")
        parts = Path(ref.storage_key).parts
        try:
            with self._directory(parts[:-1]) as directory:
                descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                with os.fdopen(descriptor, "rb") as file:
                    info = os.fstat(file.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size != ref.byte_size:
                        raise IntegrityError("Artifact integrity check failed")
                    data = file.read(ref.byte_size + 1)
            if len(data) != ref.byte_size or hashlib.sha256(data).hexdigest() != ref.sha256:
                raise IntegrityError("Artifact integrity check failed")
            return data
        except OSError:
            raise IntegrityError("Artifact integrity check failed") from None

    def orphans(
        self, registered: set[str], older_than: datetime, *, delete: bool = False
    ) -> list[str]:
        result = []
        pattern = re.compile(
            r"projects/[0-9a-f-]{36}/sha256/[0-9a-f]{2}/"
            r"(?:[0-9a-f]{64}\.(?:png|jpg|webp|json|zip|txt|bin)|\.tmp-[0-9a-f]{32})"
        )
        with self._directory(()) as root:
            for path, _, names, directory in os.fwalk(".", dir_fd=root, follow_symlinks=False):
                for name in names:
                    key = str(Path(path) / name)
                    if key in registered or not pattern.fullmatch(key):
                        continue
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_mtime >= older_than.timestamp():
                        continue
                    result.append(key)
                    if delete:
                        os.unlink(name, dir_fd=directory)
                        os.fsync(directory)
        return sorted(result)


class ArtifactStore:
    """Register only committed blobs; the caller owns the database transaction."""

    def __init__(self, uow: UnitOfWork, root: Path) -> None:
        self.uow = uow
        self.blobs = BlobStore(root)

    def put(
        self,
        project_id: UUID,
        data: bytes,
        kind: str,
        media_type: str,
        *,
        width: int | None = None,
        height: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        # Shared until transaction end: GC cannot delete a blob between reuse
        # of an old content-addressed file and commitment of its new DB reference.
        self.uow.connection.execute(
            select(func.pg_advisory_xact_lock_shared(ARTIFACT_REGISTRATION_LOCK))
        )
        ref = self.blobs.put(project_id, data, media_type)
        table = self.uow.tables.artifact_assets
        row = (
            self.uow.connection.execute(
                insert(table)
                .values(
                    project_id=project_id,
                    kind=kind,
                    storage_key=ref.storage_key,
                    sha256=ref.sha256,
                    byte_size=ref.byte_size,
                    media_type=media_type,
                    width=width,
                    height=height,
                    metadata_json=metadata or {},
                )
                .on_conflict_do_nothing(index_elements=[table.c.storage_key, table.c.kind])
                .returning(table)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            row = (
                self.uow.connection.execute(
                    select(table).where(
                        table.c.storage_key == ref.storage_key, table.c.kind == kind
                    )
                )
                .mappings()
                .one()
            )
        if row["kind"] != kind or row["sha256"] != ref.sha256 or row["byte_size"] != len(data):
            raise IntegrityError("Artifact role or integrity conflict")
        return ArtifactRef(
            project_id=project_id,
            storage_key=ref.storage_key,
            sha256=ref.sha256,
            byte_size=ref.byte_size,
            media_type=media_type,
            id=cast(UUID, row["id"]),
            kind=kind,
        )

    def read_verified(self, asset_id: UUID) -> bytes:
        table = self.uow.tables.artifact_assets
        row = (
            self.uow.connection.execute(select(table).where(table.c.id == asset_id))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise KeyError("Artifact not found")
        return self.blobs.read_verified(
            BlobRef(
                row["project_id"],
                row["storage_key"],
                row["sha256"],
                row["byte_size"],
                row["media_type"],
            )
        )

    def maintain_orphans(self, older_than: datetime, *, delete: bool = False) -> list[str]:
        self.uow.connection.execute(select(func.pg_advisory_xact_lock(ARTIFACT_REGISTRATION_LOCK)))
        registered = set(
            self.uow.connection.scalars(select(self.uow.tables.artifact_assets.c.storage_key))
        )
        return self.blobs.orphans(registered, older_than, delete=delete)
