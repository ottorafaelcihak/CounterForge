"""Private persisted SSE replay. Each poll uses a short read transaction."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from sqlalchemy import select

from .experiments import TERMINAL
from .service import Service


def replay(factory: Any, settings: Any, eid: UUID, after: int) -> tuple[list[dict[str, Any]], str]:
    with factory() as uow:
        s = Service(uow, settings)
        state = s.experiment(eid)["state"]
        t = s.t.experiment_events
        rows = (
            s.db.execute(
                select(t)
                .where(t.c.experiment_id == eid, t.c.id > after)
                .order_by(t.c.id)
                .limit(100)
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows], state


def frame(row: dict[str, Any]) -> str:
    body = {
        "id": row["id"],
        "experiment_id": str(row["experiment_id"]),
        "event": row["event_type"],
        "payload": row["payload_json"],
    }
    return f"id: {row['id']}\ndata: {json.dumps(body, separators=(',', ':'), allow_nan=False)}\n\n"


async def stream(
    request: Any, factory: Any, settings: Any, eid: UUID, after: int
) -> AsyncIterator[str]:
    keepalive = time.monotonic()
    while not await request.is_disconnected():
        rows, state = replay(factory, settings, eid, after)
        for row in rows:
            after = row["id"]
            yield frame(row)
        if len(rows) == 100:
            continue
        if state in TERMINAL:
            return
        if time.monotonic() - keepalive >= 15:
            yield ": keepalive\n\n"
            keepalive = time.monotonic()
        await asyncio.sleep(1)
