"""Bound request streams before JSON parsing or multipart temporary-file spooling."""

import re

from counterforge_contracts.versions import ErrorCode
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .artifacts import MAX_UPLOAD_BYTES
from .errors import envelope


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        upload = re.fullmatch(
            r"/api/v1/(?:projects/[^/]+/seeds|seeds/[^/]+/masks(?:/[^/]+/revisions)?)/?",
            scope["path"],
        )
        limit = MAX_UPLOAD_BYTES + 65536 if upload else 65536
        total, chunks = 0, []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > limit:
                response = JSONResponse(
                    status_code=422,
                    content=envelope(
                        ErrorCode.INVALID_INPUT, "Request body exceeds the allowed size."
                    ).model_dump(mode="json"),
                )
                await response(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        consumed = False

        async def replay() -> Message:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
