"""Pure CPU deterministic bundling with complete member scanning and verification."""

import io
import json
import re
import stat
import zlib
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

from .manifest import RegressionManifest
from .runner_template import static_members

REQUIRED = frozenset(
    {
        "evidence/original.png",
        "evidence/failing.png",
        "evidence/protected-mask.png",
        "evidence/comparison.png",
        "evidence/target-responses.json",
        "evidence/validity-report.json",
        "evidence/recipe.json",
    }
)
# Scan binary bytes too: EXIF/text chunks and additional original-source files can carry secrets.
UNSAFE = re.compile(
    rb'https?://|file://|(?:/home/|/Users/|/tmp/|/var/|[A-Z]:\\)|projects/[0-9a-f-]{36}/sha256/|\bBearer\s+[^\s\]]+|\bsk-[A-Za-z0-9_-]{8,}|\b(?:api[_-]?key|password|authorization|access[_-]?token|secret)\s*["\x27]?\s*[:=]\s*["\x27]?(?!\[redacted)[A-Za-z0-9_-]{4,}',
    re.I,
)


@dataclass(frozen=True)
class Bundle:
    zip_bytes: bytes
    manifest_bytes: bytes


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def safe_name(name: str) -> bool:
    return (
        bool(re.fullmatch(r"[A-Za-z0-9_./-]+", name))
        and not PurePosixPath(name).is_absolute()
        and all(x not in {"", ".", ".."} for x in name.split("/"))
    )


def scan(data: bytes, forbidden_values: tuple[bytes, ...] = (), *, _depth: int = 0) -> None:
    if _depth > 4:
        raise ValueError("Unsafe nested image metadata")
    if UNSAFE.search(data) or any(value and value in data for value in forbidden_values):
        raise ValueError("Unsafe secret/private reference in bundle evidence")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        # PNG zTXt/iTXt/ICC metadata is compressed independently from raster data.
        # Inspect it too; never strip unsafe original bytes to manufacture a clean export.
        offset = 8
        while offset < len(data):
            size = int.from_bytes(data[offset : offset + 4], "big")
            kind = data[offset + 4 : offset + 8]
            end = offset + 12 + size
            if end > len(data):
                raise ValueError("Unsafe malformed image metadata")
            payload = data[offset + 8 : offset + 8 + size]
            compressed = None
            try:
                if kind in {b"zTXt", b"iCCP"}:
                    _, encoded = payload.split(b"\0", 1)
                    if encoded[0] != 0:
                        raise ValueError()
                    compressed = encoded[1:]
                elif kind == b"iTXt":
                    _, encoded = payload.split(b"\0", 1)
                    flag, method = encoded[:2]
                    _, _, content = encoded[2:].split(b"\0", 2)
                    if flag not in {0, 1} or method != 0:
                        raise ValueError()
                    if flag:
                        compressed = content
                    else:
                        scan(content, forbidden_values, _depth=_depth + 1)
                if compressed is not None:
                    decoder = zlib.decompressobj()
                    decoded = decoder.decompress(compressed, 1024 * 1024)
                    if not decoder.eof or decoder.unconsumed_tail:
                        raise ValueError()
                    scan(decoded, forbidden_values, _depth=_depth + 1)
            except (ValueError, IndexError, zlib.error):
                raise ValueError("Unsafe or oversized image metadata") from None
            offset = end


def allowed_evidence(name: str) -> bool:
    return name in REQUIRED or name in {
        "evidence/original-source.png",
        "evidence/original-source.jpg",
        "evidence/original-source.webp",
    }


def build_bundle(
    manifest: RegressionManifest,
    evidence: dict[str, bytes],
    *,
    forbidden_values: tuple[bytes, ...] = (),
) -> Bundle:
    if not REQUIRED <= set(evidence):
        raise ValueError("Required evidence is missing")
    if any(not safe_name(n) or not n.startswith("evidence/") for n in evidence):
        raise ValueError("Unsafe bundle member path")
    if any(not allowed_evidence(name) for name in evidence):
        raise ValueError("Unexpected evidence member")
    if len(evidence) > 32 or sum(map(len, evidence.values())) > 100 * 1024 * 1024:
        raise ValueError("Evidence member size limit")
    for name, data in evidence.items():
        if not data:
            raise ValueError("Required evidence is empty")
        scan(data, forbidden_values)
        if name in manifest.sha256 and manifest.sha256[name] != sha256(data).hexdigest():
            raise ValueError("Evidence hash mismatch")
    members = {**evidence, **static_members()}
    digests = {name: sha256(data).hexdigest() for name, data in sorted(members.items())}
    if any(not safe_name(name) or name not in evidence for name in manifest.files.values()):
        raise ValueError("Invalid manifest file binding")
    public = manifest.model_copy(update={"sha256": digests})
    body = canonical(public.model_dump(mode="json"))
    scan(body, forbidden_values)
    members["manifest.json"] = body
    members["sha256sums.txt"] = "".join(
        f"{sha256(data).hexdigest()}  {name}\n" for name, data in sorted(members.items())
    ).encode()
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(members.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compresslevel=9)
    result = Bundle(output.getvalue(), body)
    verify_bundle(result.zip_bytes, forbidden_values=forbidden_values)
    return result


def verify_bundle(data: bytes, *, forbidden_values: tuple[bytes, ...] = ()) -> None:
    try:
        if len(data) > 100 * 1024 * 1024:
            raise ValueError("Bundle member size limit")
        with ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if (
                len(infos) > 40
                or sum(x.file_size for x in infos) > 100 * 1024 * 1024
                or any(
                    stat.S_IFMT(x.external_attr >> 16) not in {0, stat.S_IFREG} or x.flag_bits & 1
                    for x in infos
                )
            ):
                raise ValueError("Unsafe or oversized bundle member")
            names = archive.namelist()
            allowed = set(static_members()) | {"manifest.json", "sha256sums.txt"}
            if any(n not in allowed and not allowed_evidence(n) for n in names):
                raise ValueError("Unexpected bundle member")
            if len(names) != len(set(names)) or any(not safe_name(n) for n in names):
                raise ValueError("Unsafe bundle members")
            sums = {}
            for line in archive.read("sha256sums.txt").decode().splitlines():
                digest, name = line.split("  ")
                if name in sums or not re.fullmatch("[0-9a-f]{64}", digest):
                    raise ValueError("Invalid checksums")
                sums[name] = digest
            if set(sums) != set(names) - {"sha256sums.txt"}:
                raise ValueError("Incomplete checksum set")
            for name, digest in sums.items():
                if sha256(archive.read(name)).hexdigest() != digest:
                    raise ValueError("Bundle hash mismatch")
            manifest = RegressionManifest.model_validate_json(archive.read("manifest.json"))
            if manifest.sha256 != {k: v for k, v in sums.items() if k != "manifest.json"}:
                raise ValueError("Manifest hash binding failed")
            templates = static_members()
            if any(archive.read(k) != v for k, v in templates.items()):
                raise ValueError("Untrusted runner source")
            for name in names:
                if name not in templates:
                    scan(archive.read(name), forbidden_values)
                elif any(value and value in archive.read(name) for value in forbidden_values):
                    raise ValueError("Unsafe secret in static bundle member")
            if not REQUIRED <= set(names):
                raise ValueError("Required evidence missing")
    except (KeyError, OSError, UnicodeError, BadZipFile) as exc:
        raise ValueError("Invalid bundle") from exc
