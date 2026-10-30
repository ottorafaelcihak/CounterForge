"""Canonical hashes and atomic, fsynced, no-secret evaluation artifacts."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID


def json_default(value: Any) -> str:
    if isinstance(value, (datetime, UUID, Decimal)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    raise TypeError(f"Unsupported JSON type: {type(value).__name__}")


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=json_default,
    ).encode()


def digest(value: Any) -> str:
    return sha256(canonical(value)).hexdigest()


def seal(value: dict[str, Any]) -> dict[str, Any]:
    unsigned = {k: v for k, v in value.items() if k != "sha256"}
    return {**unsigned, "sha256": digest(unsigned)}


def verify(value: dict[str, Any]) -> None:
    if value.get("sha256") != seal(value)["sha256"]:
        raise ValueError("Frozen manifest integrity mismatch")


def aware_past(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None or value > datetime.now(UTC):
        raise ValueError("Human review requires an aware, non-future timestamp")
    return value


def real_text(value: str) -> str:
    text = value.strip()
    if not text or text.casefold() in {"pending", "todo", "unknown", "tbd"}:
        raise ValueError("Human evidence remains pending")
    return text


def atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
