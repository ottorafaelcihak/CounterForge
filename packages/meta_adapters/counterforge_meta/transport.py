"""Bounded Meta wire client. No configuration, secret or database discovery."""

from __future__ import annotations

import asyncio
import base64
import random
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from hashlib import sha256
from io import BytesIO
from typing import Any, Self, cast

import httpx
from counterforge_contracts.providers import ProviderResult
from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from PIL import Image, UnidentifiedImageError
from pydantic import JsonValue

from .usage import (
    Accounting,
    CallIntent,
    Category,
    Charge,
    Outcome,
    PriceBook,
    quiet_wire_logs,
    sanitize_raw,
    semantic_digest,
)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 16 * 1024 * 1024


def inspect_image(data: bytes) -> tuple[str, int, int]:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("invalid image size")
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
                image.format or ""
            )
            if mime is None or width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise ValueError("invalid image geometry or format")
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("invalid image") from error
    return mime, width, height


def image_data_url(data: bytes) -> str:
    mime, _, _ = inspect_image(data)
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


class DeadlineBeforeSend(Exception):
    """The claim completed after the absolute wire deadline."""


class WireLimitError(Exception):
    """Fixed error without provider content."""


class BoundedTransport(httpx.AsyncBaseTransport):
    """Read raw bytes under a hard quota before SDK JSON or SSE parsing."""

    def __init__(self, inner: httpx.AsyncBaseTransport, *, api_key: str) -> None:
        self.inner = inner
        self._key = api_key

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if (
            request.url.scheme != "https"
            or request.url.host != "api.meta.ai"
            or request.url.port not in (None, 443)
            or request.url.username
            or request.url.password
            or not request.url.path.startswith("/v1/")
        ):
            raise WireLimitError("unexpected endpoint")
        body = await request.aread()
        if len(body) > 16 * 1024 * 1024:
            raise WireLimitError("request too large")
        content_type = "application/json"
        if "/images/" in request.url.path:
            boundary_line = body.split(b"\r\n", 1)[0]
            if not re.fullmatch(rb"--[A-Za-z0-9'()+_,./:=?-]{1,70}", boundary_line):
                raise WireLimitError("invalid multipart body")
            content_type = (
                'multipart/form-data; boundary="' + boundary_line[2:].decode("ascii") + '"'
            )
        generated = {"content-type": content_type, "content-length": str(len(body))}
        request.headers.clear()
        request.headers.update(
            {
                **generated,
                "host": "api.meta.ai",
                "authorization": f"Bearer {self._key}",
                "accept": "application/json",
                "accept-encoding": "identity",
                "user-agent": "CounterForge/0.1",
            }
        )
        response = await self.inner.handle_async_request(request)
        try:
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise WireLimitError("unexpected compression")
            cap = (
                16 * 1024 * 1024
                if "/images/" in request.url.path
                else 4 * 1024 * 1024
                if request.url.path.endswith("/responses")
                else 1024 * 1024
            )
            if response.status_code >= 400:
                cap = 64 * 1024
            length = response.headers.get("content-length")
            if length is not None and (not length.isdecimal() or int(length) > cap):
                raise WireLimitError("response too large")
            chunks: list[bytes] = []
            total = 0

            async def chunks_from_response() -> AsyncIterator[bytes]:
                if response.is_stream_consumed:
                    yield response.content
                else:
                    async for part in response.aiter_raw():
                        yield part

            async for chunk in chunks_from_response():
                total += len(chunk)
                if total > cap:
                    raise WireLimitError("response too large")
                chunks.append(chunk)
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                content=b"".join(chunks),
                extensions=response.extensions,
            )
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        await self.inner.aclose()


@dataclass(frozen=True)
class CallResponse:
    outcome: Outcome
    metadata: ProviderResult
    data: dict[str, Any] = field(default_factory=dict, repr=False)
    events: tuple[dict[str, Any], ...] = field(default=(), repr=False)


class MetaTransport:
    def __init__(
        self,
        *,
        api_key: str,
        accounting: Accounting,
        prices: PriceBook,
        http_transport: httpx.AsyncBaseTransport | None = None,
        deadline_seconds: float = 45,
        unbilled_429: bool = False,
    ) -> None:
        quiet_wire_logs()
        if not api_key or not 0 < deadline_seconds <= 120:
            raise ValueError("invalid transport configuration")
        self.accounting, self.prices = accounting, prices
        self.deadline_seconds, self.unbilled_429 = deadline_seconds, unbilled_429
        self._secrets = (api_key,)
        self._http = httpx.AsyncClient(
            transport=BoundedTransport(
                http_transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False),
                api_key=api_key,
            ),
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(connect=5, read=20, write=20, pool=5),
        )
        self._client = AsyncOpenAI(
            api_key=api_key,
            admin_api_key="",
            organization="",
            project="",
            webhook_secret="",
            base_url="https://api.meta.ai/v1",
            http_client=self._http,
            max_retries=0,
            timeout=httpx.Timeout(connect=5, read=20, write=20, pool=5),
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.close()

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        category: Category,
        template_version: str,
        response_format: dict[str, Any] | None = None,
        max_completion_tokens: int = 512,
        temperature: float | None = None,
        top_p: float | None = None,
    ) -> CallResponse:
        model = "muse-spark-1.3"
        self.prices.upper(model, max_completion_tokens)
        options: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_completion_tokens,
        }
        for key, value, maximum in (("temperature", temperature, 2.0), ("top_p", top_p, 1.0)):
            if value is not None:
                if (
                    type(value) not in (int, float)
                    or not 0 <= value <= maximum
                    or (key == "top_p" and value == 0)
                ):
                    raise ValueError("invalid sampling option")
                options[key] = value
        if response_format is not None:
            options["response_format"] = response_format

        async def send() -> tuple[dict[str, Any], tuple[dict[str, Any], ...], str | None]:
            reply = await self._client.chat.completions.create(**cast(Any, options))
            return reply.model_dump(mode="json"), (), getattr(reply, "_request_id", None)

        return await self._call(
            model, category, options, template_version, send, max_completion_tokens
        )

    async def image(
        self, *, source_bytes: bytes, instruction: str, template_version: str = "scene-v1"
    ) -> CallResponse:
        mime, _, _ = inspect_image(source_bytes)
        if not 1 <= len(instruction) <= 4000:
            raise ValueError("invalid image instruction")
        options = {
            "model": "muse-image-1.0",
            "prompt": instruction,
            "n": 1,
            "response_format": "b64_json",
            "output_format": "png",
            "source_sha256": sha256(source_bytes).hexdigest(),
        }

        async def send() -> tuple[dict[str, Any], tuple[dict[str, Any], ...], str | None]:
            reply = await self._client.images.edit(
                model="muse-image-1.0",
                image=("source", source_bytes, mime),
                prompt=instruction,
                n=1,
                response_format="b64_json",
                output_format="png",
            )
            return reply.model_dump(mode="json"), (), getattr(reply, "_request_id", None)

        return await self._call("muse-image-1.0", "IMAGE", options, template_version, send)

    async def sam(
        self, *, image_bytes: bytes, concept: str, template_version: str = "sam-v1"
    ) -> CallResponse:
        if not 1 <= len(concept) <= 200:
            raise ValueError("invalid segmentation concept")
        media = image_data_url(image_bytes)
        options = {
            "model": "sam-3.1",
            "concept": concept,
            "image_sha256": sha256(image_bytes).hexdigest(),
            "stream": True,
        }

        async def send() -> tuple[dict[str, Any], tuple[dict[str, Any], ...], str | None]:
            stream = await self._client.responses.create(
                model="sam-3.1",
                stream=True,
                input=cast(
                    Any,
                    [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": concept},
                                {"type": "input_image", "image_url": media},
                            ],
                        }
                    ],
                ),
            )
            events: list[dict[str, Any]] = []
            async with stream:
                async for event in stream:
                    if len(events) >= 4096:
                        raise WireLimitError("too many stream events")
                    events.append(event.model_dump(mode="json", exclude_none=True))
            terminal: dict[str, Any] = next(
                (
                    e.get("response", {})
                    for e in reversed(events)
                    if e.get("type")
                    in {"response.completed", "response.incomplete", "response.failed"}
                ),
                {},
            )
            return terminal, tuple(events), stream.response.headers.get("x-request-id")

        return await self._call("sam-3.1", "SAM", options, template_version, send)

    async def _call(
        self,
        model: str,
        category: Category,
        options: dict[str, Any],
        template_version: str,
        send: Callable[
            [], Awaitable[tuple[dict[str, Any], tuple[dict[str, Any], ...], str | None]]
        ],
        max_completion_tokens: int = 512,
    ) -> CallResponse:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.deadline_seconds
        upper = self.prices.upper(model, max_completion_tokens)
        digest = semantic_digest(
            {
                "endpoint": "https://api.meta.ai/v1"
                + {
                    "muse-spark-1.3": "/chat/completions",
                    "muse-image-1.0": "/images/edits",
                    "sam-3.1": "/responses",
                }[model],
                "options": options,
                "template": template_version,
                "pricing": self.prices.revision,
            }
        )
        for attempt in range(1, 4):
            intent = CallIntent(model, category, digest, self.prices.revision, upper, attempt)
            ticket = await self.accounting.reserve(intent)
            started = loop.time()
            if not await ticket.claim():
                return CallResponse(
                    "NOT_DISPATCHED",
                    ProviderResult(
                        provider="META", model=model, latency_ms=0, warnings=("CLAIM_NOT_ACQUIRED",)
                    ),
                )
            raw: dict[str, Any] = {}
            data: dict[str, Any] = {}
            events: tuple[dict[str, Any], ...] = ()
            request_id = None
            retry_delay: float | None = None
            cancelled = False
            outcome: Outcome = "UNKNOWN"
            charge = Charge("UNKNOWN", upper, True, "AMBIGUOUS_DISPATCH")
            try:
                if loop.time() >= deadline:
                    outcome = "NOT_DISPATCHED"
                    charge = Charge("UNBILLED", DecimalZero, False, "DEADLINE_BEFORE_SEND")
                    raise DeadlineBeforeSend
                async with asyncio.timeout_at(deadline):
                    data, events, request_id = await send()
                raw = data
                outcome = self._outcome(model, data, events)
                if model == "muse-spark-1.3":
                    charge = self.prices.token_charge(
                        model, data.get("usage"), upper, max_completion_tokens
                    )
                elif (
                    model == "muse-image-1.0"
                    and isinstance(data.get("data"), list)
                    and len(data["data"]) <= 1
                ):
                    outputs = data["data"]
                    if not outputs or (
                        isinstance(outputs[0], dict)
                        and (outputs[0].get("b64_json") or outputs[0].get("url"))
                    ):
                        charge = Charge(
                            "ACTUAL",
                            self.prices.rate(model, "per_image") * len(outputs),
                            True,
                            "COMPLETED_IMAGES",
                        )
                    else:
                        charge = Charge("ESTIMATED", upper, True, "MISSING_USAGE")
                elif model == "sam-3.1" and not any(
                    event.get("type")
                    in {"response.completed", "response.incomplete", "response.failed"}
                    for event in events
                ):
                    charge = Charge("UNKNOWN", upper, True, "AMBIGUOUS_DISPATCH")
                elif model == "sam-3.1" and outcome == "COMPLETED":
                    charge = Charge(
                        "ACTUAL", self.prices.rate(model, "per_image"), True, "COMPLETED_IMAGES"
                    )
                else:
                    charge = Charge("ESTIMATED", upper, True, "MISSING_USAGE")
                if data.get("model") not in (None, model) or (
                    model == "muse-spark-1.3" and data.get("model") != model
                ):
                    outcome = "INVALID"
                    charge = Charge("UNKNOWN", upper, True, "INVALID_RESPONSE")
            except DeadlineBeforeSend:
                pass
            except APIStatusError as error:
                request_id = error.response.headers.get("x-request-id")
                outcome = (
                    "RATE_LIMITED"
                    if error.status_code == 429
                    else "UNKNOWN"
                    if error.status_code >= 500
                    else "PROVIDER_ERROR"
                )
                if error.status_code == 429 and self.unbilled_429:
                    charge = Charge("UNBILLED", DecimalZero, True, "UNBILLED_RATE_LIMIT")
                    body = error.body
                    code = body.get("code") if isinstance(body, dict) else None
                    if code != "insufficient_quota":
                        retry_delay = self._retry_after(
                            error.response.headers.get("retry-after"), attempt
                        )
            except APIConnectionError as error:
                if isinstance(
                    error.__cause__, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
                ):
                    outcome = "PROVIDER_ERROR"
                    charge = Charge("UNBILLED", DecimalZero, False, "CONNECT_FAILED")
                    retry_delay = random.uniform(0.01, 0.05) * 2 ** (attempt - 1)
            except asyncio.CancelledError:
                charge = Charge("UNKNOWN", upper, True, "CANCELLED")
                cancelled = True
            except Exception:
                pass
            latency = max(0, int((loop.time() - started) * 1000))
            safe_id = (
                request_id
                if isinstance(request_id, str)
                and re.fullmatch(r"[\w.:-]{1,128}", request_id)
                and not any(s in request_id for s in self._secrets)
                else None
            )
            metadata: dict[str, JsonValue] = {
                "latency_ms": latency,
                "request_id": safe_id,
                "usage": sanitize_raw({"usage": data.get("usage")}, secrets=self._secrets).get(
                    "usage"
                )
                or {},
                "warnings": [f"OUTCOME_{outcome}", f"PRICING_{self.prices.revision}"],
            }
            # Shield durable finalization if the caller cancels after dispatch. Parent must
            # quarantine a claimed ticket if this persistence step itself fails.
            finishing = asyncio.create_task(
                ticket.finish(
                    outcome,
                    charge,
                    metadata,
                    sanitize_raw(
                        {
                            "response": raw,
                            "template_version": template_version,
                            "pricing_revision": self.prices.revision,
                        },
                        secrets=self._secrets,
                    ),
                )
            )
            try:
                result = await asyncio.shield(finishing)
            except asyncio.CancelledError:
                await finishing
                raise
            if cancelled:
                raise asyncio.CancelledError
            if retry_delay is not None and attempt < 3 and loop.time() + retry_delay < deadline:
                await asyncio.sleep(retry_delay)
                continue
            return CallResponse(outcome, result, data, events)
        raise AssertionError("unreachable")

    @staticmethod
    def _outcome(model: str, data: dict[str, Any], events: tuple[dict[str, Any], ...]) -> Outcome:
        if model == "muse-spark-1.3":
            choices = data.get("choices")
            if (
                not isinstance(choices, list)
                or len(choices) != 1
                or not isinstance(choices[0], dict)
            ):
                return "INVALID"
            choice = choices[0]
            message = choice.get("message")
            if not isinstance(message, dict):
                return "INVALID"
            if message.get("refusal") or choice.get("finish_reason") == "content_filter":
                return "REFUSED"
            if choice.get("finish_reason") != "stop":
                return "INCOMPLETE"
            return (
                "COMPLETED"
                if isinstance(message.get("content"), str) and message["content"]
                else "INVALID"
            )
        if model == "sam-3.1":
            if any(e.get("type", "").startswith("response.refusal") for e in events):
                return "REFUSED"
            if any(e.get("type") in {"error", "response.failed"} for e in events):
                return "PROVIDER_ERROR"
            return (
                "COMPLETED"
                if events and events[-1].get("type") == "response.completed"
                else "INCOMPLETE"
            )
        outputs = data.get("data")
        return (
            "COMPLETED"
            if isinstance(outputs, list)
            and len(outputs) == 1
            and isinstance(outputs[0], dict)
            and (outputs[0].get("b64_json") or outputs[0].get("url"))
            else "INVALID"
        )

    @staticmethod
    def _retry_after(value: str | None, attempt: int) -> float:
        if value is not None:
            try:
                seconds = float(value)
                if 0 <= seconds < float("inf"):
                    return seconds
            except ValueError:
                try:
                    return max(
                        0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
                    )
                except (ValueError, TypeError, OverflowError):
                    pass
        return float(random.uniform(0.1, 0.2) * 2 ** (attempt - 1))


DecimalZero = Decimal("0")
