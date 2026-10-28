"""No-network accounting and wire-boundary regressions."""

from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from counterforge_contracts.providers import ProviderResult


class Ledger:
    def __init__(self, *, claim=True, cap=99):
        self.intents = []
        self.finishes = []
        self.claims = []
        self.allowed = claim
        self.cap = cap

    async def reserve(self, intent):
        if len(self.intents) >= self.cap:
            raise ValueError("budget exhausted")
        self.intents.append(intent)
        parent = self

        class Ticket:
            async def claim(self):
                parent.claims.append(intent.attempt)
                return parent.allowed

            async def finish(self, outcome, charge, metadata, sanitized_raw):
                parent.finishes.append((outcome, charge, metadata, sanitized_raw))
                return ProviderResult(provider="META", model=intent.model, **metadata)

        return Ticket()


def prices():
    from counterforge_meta.usage import PriceBook

    return PriceBook.load(Path(__file__).resolve().parents[2] / "pricing.toml")


def chat_body(*, usage=True, content="5", finish="stop"):
    body = {
        "id": "chat-fixture",
        "model": "muse-spark-1.3",
        "created": 1,
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "content": content},
            }
        ],
    }
    if usage:
        body["usage"] = {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_tokens_details": {"cached_tokens": 40},
        }
    return body


async def run_chat(handler, ledger=None, **kwargs):
    from counterforge_meta.transport import MetaTransport

    book = prices()
    ledger = ledger or Ledger()
    async with MetaTransport(
        api_key="fixture-key",
        accounting=ledger,
        prices=book,
        http_transport=httpx.MockTransport(handler),
        **kwargs,
    ) as transport:
        result = await transport.chat(
            messages=[{"role": "user", "content": "count"}],
            category="TARGET",
            template_version="target-v1",
        )
    return result, ledger


async def test_preconnect_retries_new_claimed_tickets_and_preserves_evidence():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("secret https://bad.invalid/?key=secret", request=request)
        return httpx.Response(200, json=chat_body(), headers={"x-request-id": "req-fixture"})

    result, ledger = await run_chat(handler)
    assert result.outcome == "COMPLETED"
    assert ledger.claims == [1, 2, 3]
    assert [i.attempt for i in ledger.intents] == [1, 2, 3]
    assert len({i.semantic_sha256 for i in ledger.intents}) == 1
    assert [f[1].kind for f in ledger.finishes] == ["UNBILLED", "UNBILLED", "ACTUAL"]
    assert [f[1].request_sent for f in ledger.finishes] == [False, False, True]
    assert ledger.finishes[-1][1].amount_usd == Decimal("0.000166")
    assert result.metadata.request_id == "req-fixture"
    assert ledger.intents[0].upper_bound_usd >= Decimal("1.31")
    assert "secret" not in repr(ledger.finishes)


@pytest.mark.parametrize(
    "exception", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError]
)
async def test_ambiguous_failure_is_unknown_without_retry(exception):
    def handler(request):
        raise exception("fixture-key https://bad.invalid/?secret=1", request=request)

    result, ledger = await run_chat(handler)
    assert result.outcome == "UNKNOWN"
    assert len(ledger.intents) == 1
    assert ledger.finishes[0][1].kind == "UNKNOWN"
    assert ledger.finishes[0][1].request_sent is True
    assert "fixture-key" not in repr(result)


async def test_missing_usage_is_estimated_never_zero():
    _, ledger = await run_chat(lambda r: httpx.Response(200, json=chat_body(usage=False)))
    charge = ledger.finishes[0][1]
    assert charge.kind == "ESTIMATED"
    assert charge.amount_usd > Decimal("1")


async def test_claim_denial_never_dispatches():
    def handler(request):
        pytest.fail("unclaimed call dispatched")

    result, ledger = await run_chat(handler, Ledger(claim=False))
    assert result.outcome == "NOT_DISPATCHED"
    assert ledger.finishes == []  # existing ticket belongs to its successful claimant


async def test_rate_limit_requires_explicit_unbilled_policy_and_deadline():
    result, ledger = await run_chat(
        lambda r: httpx.Response(
            429, headers={"retry-after": "60"}, json={"error": {"message": "key=secret"}}
        ),
        unbilled_429=True,
        deadline_seconds=0.1,
    )
    assert result.outcome == "RATE_LIMITED"
    assert len(ledger.intents) == 1
    assert ledger.finishes[0][1].kind == "UNBILLED"
    _, ledger = await run_chat(lambda r: httpx.Response(429, json={"error": {"message": "secret"}}))
    assert ledger.finishes[0][1].kind == "UNKNOWN"
    assert len(ledger.intents) == 1


async def test_rate_limit_retry_finalizes_prior_attempt_before_new_reserve():
    ledger = Ledger()

    def handler(request):
        if len(ledger.intents) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": {}})
        assert len(ledger.finishes) == 1
        return httpx.Response(200, json=chat_body())

    result, _ = await run_chat(handler, ledger, unbilled_429=True)
    assert result.outcome == "COMPLETED"
    assert ledger.claims == [1, 2]


@pytest.mark.parametrize(
    "status,body", [(500, {"error": {"message": "secret"}}), (200, {"choices": []})]
)
async def test_server_and_shape_errors_are_normalized(status, body):
    result, ledger = await run_chat(lambda r: httpx.Response(status, json=body))
    assert result.outcome in {"UNKNOWN", "INVALID"}
    assert len(ledger.intents) == 1
    assert "secret" not in repr(ledger.finishes)


async def test_wire_bounds_applied_before_sdk_decode_and_compression_rejected():
    for headers, body in [({}, b"x" * (1024 * 1024 + 1)), ({"content-encoding": "gzip"}, b"bad")]:
        result, ledger = await run_chat(
            lambda r, body=body, headers=headers: httpx.Response(200, content=body, headers=headers)
        )
        assert result.outcome == "UNKNOWN"
        assert len(ledger.intents) == 1


async def test_finite_deadline_settles_cancelled_dispatched_call():
    import asyncio

    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=chat_body())

    result, ledger = await run_chat(handler, deadline_seconds=0.02)
    assert result.outcome == "UNKNOWN"
    assert ledger.finishes[0][1].kind == "UNKNOWN"


async def test_semantic_identity_changes_with_prompt_and_template():
    from counterforge_meta.transport import MetaTransport

    ledger = Ledger()
    async with MetaTransport(
        api_key="fixture",
        accounting=ledger,
        prices=prices(),
        http_transport=httpx.MockTransport(lambda r: httpx.Response(200, json=chat_body())),
    ) as t:
        for prompt, template in [("a", "v1"), ("b", "v1"), ("a", "v2")]:
            await t.chat(
                messages=[{"role": "user", "content": prompt}],
                category="TARGET",
                template_version=template,
            )
    assert len({i.semantic_sha256 for i in ledger.intents}) == 3


def test_recursive_raw_sanitization_drops_nested_secrets_urls_and_media():
    from counterforge_meta.usage import sanitize_raw

    raw = {
        "id": "req-safe",
        "usage": {"prompt_tokens": 5},
        "nested": [
            {
                "headers": {"authorization": "secret"},
                "url": "https://cdn.invalid/?signed=secret",
                "b64_json": "AA==",
                "text": "Bearer secret",
            }
        ],
    }
    safe = sanitize_raw(raw)
    assert safe["id"] == "req-safe"
    assert "secret" not in str(safe)
    assert "https" not in str(safe)
    assert "AA==" not in str(safe)


async def test_transport_does_not_forward_environment_headers(monkeypatch):
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS",
        "X-Leak: secret\nAuthorization: Bearer wrong\nOpenAI-Organization: secret-org\nContent-Type: evil\nContent-Length: 0",
    )
    monkeypatch.setenv("OPENAI_ORG_ID", "secret-org")

    def handler(request):
        assert request.url.host == "api.meta.ai"
        assert request.headers["authorization"] == "Bearer fixture-key"
        assert "x-leak" not in request.headers
        assert "openai-organization" not in request.headers
        assert request.headers["accept-encoding"] == "identity"
        assert request.headers["content-type"] == "application/json"
        assert int(request.headers["content-length"]) == len(request.content)
        return httpx.Response(200, json=chat_body())

    result, _ = await run_chat(handler)
    assert result.outcome == "COMPLETED"


async def test_transport_stream_quota_stops_without_consuming_huge_body():
    yielded = 0

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal yielded
            for _ in range(50):
                yielded += 1
                yield b"x" * 65536

    result, _ = await run_chat(lambda r: httpx.Response(200, stream=Stream()))
    assert result.outcome == "UNKNOWN"
    assert yielded == 17


async def test_429_quota_exhaustion_is_not_retried_even_under_unbilled_policy():
    result, ledger = await run_chat(
        lambda r: httpx.Response(
            429, json={"error": {"code": "insufficient_quota", "message": "secret"}}
        ),
        unbilled_429=True,
    )
    assert result.outcome == "RATE_LIMITED"
    assert len(ledger.intents) == 1
    assert ledger.finishes[0][1].kind == "UNBILLED"


async def test_retry_cap_exception_stops_further_dispatch():
    ledger = Ledger(cap=1)
    with pytest.raises(ValueError, match="budget exhausted"):
        await run_chat(
            lambda r: (_ for _ in ()).throw(httpx.ConnectError("offline", request=r)), ledger
        )
    assert len(ledger.finishes) == 1
    assert ledger.finishes[0][1].kind == "UNBILLED"


async def test_target_options_sent_and_semantically_hashed():
    from counterforge_meta.transport import MetaTransport

    ledger = Ledger()

    def handler(request):
        import json

        body = json.loads(request.content)
        assert body["temperature"] in {0.0, 0.2}
        assert body["top_p"] == 0.9
        return httpx.Response(200, json=chat_body())

    async with MetaTransport(
        api_key="fixture",
        accounting=ledger,
        prices=prices(),
        http_transport=httpx.MockTransport(handler),
    ) as t:
        for temperature in [0.0, 0.2]:
            await t.chat(
                messages=[],
                category="TARGET",
                template_version="v1",
                temperature=temperature,
                top_p=0.9,
            )
    assert ledger.intents[0].semantic_sha256 != ledger.intents[1].semantic_sha256


async def test_image_edit_multipart_fixed_count_tariff_ignores_token_usage():
    import base64
    from io import BytesIO

    from counterforge_meta.transport import MetaTransport
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (3, 2)).save(buffer, format="PNG")
    image = buffer.getvalue()
    ledger = Ledger()

    def handler(request):
        assert request.url.path == "/v1/images/edits"
        assert request.headers["content-type"].startswith("multipart/form-data;")
        assert b'name="n"\r\n\r\n1' in request.content
        assert b'name="response_format"\r\n\r\nb64_json' in request.content
        assert b'name="output_format"\r\n\r\npng' in request.content
        return httpx.Response(
            200,
            json={
                "created": 1,
                "data": [{"b64_json": base64.b64encode(image).decode()}],
                "usage": {"input_tokens": 999999, "output_tokens": 999999},
            },
        )

    async with MetaTransport(
        api_key="fixture",
        accounting=ledger,
        prices=prices(),
        http_transport=httpx.MockTransport(handler),
    ) as t:
        result = await t.image(source_bytes=image, instruction="Background only")
    assert result.outcome == "COMPLETED"
    assert ledger.finishes[0][1].kind == "ACTUAL"
    assert ledger.finishes[0][1].amount_usd == Decimal("0.01")
    assert ledger.intents[0].upper_bound_usd == Decimal("0.012")
    assert base64.b64encode(image).decode() not in repr(ledger.finishes)


async def test_empty_image_filtered_outputs_are_zero_fixed_charge():
    from io import BytesIO

    from counterforge_meta.transport import MetaTransport
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (3, 2)).save(buffer, format="PNG")
    ledger = Ledger()
    async with MetaTransport(
        api_key="fixture",
        accounting=ledger,
        prices=prices(),
        http_transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"created": 1, "data": []})
        ),
    ) as t:
        result = await t.image(source_bytes=buffer.getvalue(), instruction="Background only")
    assert result.outcome == "INVALID"
    assert ledger.finishes[0][1].kind == "ACTUAL"
    assert ledger.finishes[0][1].amount_usd == 0


async def test_sam_sse_uses_response_endpoint_preserves_requestid_and_fixed_price():
    import json
    from io import BytesIO

    from counterforge_meta.transport import MetaTransport
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (3, 2)).save(buffer, format="PNG")
    ledger = Ledger()
    lane = {"item_id": "msg-fixture", "output_index": 0, "content_index": 0}
    events = [
        {"type": "response.output_text.done", **lane, "text": "fixture"},
        {
            "type": "response.completed",
            "response": {"id": "response-fixture", "status": "completed"},
        },
    ]

    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/responses"
        assert body["model"] == "sam-3.1"
        assert body["stream"] is True
        assert body["input"][0]["content"][0] == {"type": "input_text", "text": "container"}
        assert body["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")
        sse = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
        return httpx.Response(
            200,
            text=sse,
            headers={"content-type": "text/event-stream", "x-request-id": "req-sam-fixture"},
        )

    async with MetaTransport(
        api_key="fixture-key",
        accounting=ledger,
        prices=prices(),
        http_transport=httpx.MockTransport(handler),
    ) as t:
        result = await t.sam(image_bytes=buffer.getvalue(), concept="container")
    assert result.outcome == "COMPLETED"
    assert len(result.events) == 2
    assert result.metadata.request_id == "req-sam-fixture"
    assert ledger.finishes[0][1].amount_usd == Decimal("0.0025")
    assert ledger.finishes[0][1].kind == "ACTUAL"


async def test_caller_cancellation_after_wire_finalizes_unknown_ticket():
    import asyncio

    from counterforge_meta.transport import MetaTransport

    entered = asyncio.Event()

    async def handler(request):
        entered.set()
        await asyncio.sleep(10)
        return httpx.Response(200, json=chat_body())

    ledger = Ledger()
    async with MetaTransport(
        api_key="fixture",
        accounting=ledger,
        prices=prices(),
        http_transport=httpx.MockTransport(handler),
    ) as t:
        task = asyncio.create_task(t.chat(messages=[], category="TARGET", template_version="v1"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(ledger.finishes) == 1
    assert ledger.finishes[0][1].kind == "UNKNOWN"
    assert ledger.finishes[0][1].reason == "CANCELLED"


async def test_mismatched_sku_cannot_be_silently_claimed_at_standard_tariff():
    body = chat_body()
    body["model"] = "muse-spark-1.3-contributor"
    result, ledger = await run_chat(lambda r: httpx.Response(200, json=body))
    assert result.outcome == "INVALID"
    assert ledger.finishes[0][1].kind == "UNKNOWN"


async def test_usage_above_requested_output_cap_cannot_claim_actual():
    body = chat_body()
    body["usage"]["completion_tokens"] = 4096
    _, ledger = await run_chat(lambda r: httpx.Response(200, json=body))
    assert ledger.finishes[0][1].kind == "ESTIMATED"


async def test_spark_upper_bound_does_not_assume_vision_input_estimate():
    _, ledger = await run_chat(lambda r: httpx.Response(200, json=chat_body()))
    # Full 1,048,576 input context at $1.25/M + 512 output at $4.25/M.
    assert ledger.intents[0].upper_bound_usd == Decimal("1.312896")


async def test_expired_deadline_after_claim_never_dispatches_and_is_proven_unbilled():
    import asyncio

    class SlowLedger(Ledger):
        async def reserve(self, intent):
            ticket = await super().reserve(intent)
            original = ticket.claim

            async def claim():
                result = await original()
                await asyncio.sleep(0.03)
                return result

            ticket.claim = claim
            return ticket

    def handler(request):
        pytest.fail("expired call dispatched")

    result, ledger = await run_chat(handler, SlowLedger(), deadline_seconds=0.01)
    assert result.outcome == "NOT_DISPATCHED"
    assert ledger.finishes[0][1].kind == "UNBILLED"
    assert ledger.finishes[0][1].request_sent is False


async def test_sam_eof_without_terminal_event_keeps_unknown_charge():
    import json
    from io import BytesIO

    from counterforge_meta.transport import MetaTransport
    from PIL import Image

    image = BytesIO()
    Image.new("RGB", (3, 2)).save(image, format="PNG")
    event = {
        "type": "response.output_text.delta",
        "item_id": "msg-fixture",
        "output_index": 0,
        "content_index": 0,
        "delta": "partial",
    }
    ledger = Ledger()
    async with MetaTransport(
        api_key="fixture-key",
        accounting=ledger,
        prices=prices(),
        http_transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                text="data: " + json.dumps(event) + "\n\n",
                headers={"content-type": "text/event-stream"},
            )
        ),
    ) as transport:
        result = await transport.sam(image_bytes=image.getvalue(), concept="container")
    assert result.outcome == "INCOMPLETE"
    assert len(ledger.intents) == 1
    assert ledger.finishes[0][1].kind == "UNKNOWN"
    assert ledger.finishes[0][1].request_sent is True
    assert ledger.finishes[0][1].amount_usd == Decimal("0.003")


async def test_third_party_debug_logging_cannot_emit_request_media_or_keys(caplog):
    import logging

    from counterforge_meta.transport import MetaTransport

    caplog.set_level(logging.DEBUG, logger="openai._base_client")
    caplog.set_level(logging.INFO, logger="httpx")
    async with MetaTransport(
        api_key="PRIVATE_MODEL_KEY",
        accounting=Ledger(),
        prices=prices(),
        http_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=chat_body())),
    ) as client:
        await client.chat(
            messages=[{"role": "user", "content": "PRIVATE_IMAGE_DATA"}],
            category="TARGET",
            template_version="privacy-v1",
        )
    assert "PRIVATE_MODEL_KEY" not in caplog.text and "PRIVATE_IMAGE_DATA" not in caplog.text


async def test_transport_rejects_zero_top_p_before_any_reservation():
    from counterforge_meta.transport import MetaTransport

    ledger = Ledger()
    async with MetaTransport(
        api_key="fixture-key",
        accounting=ledger,
        prices=prices(),
        http_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=chat_body())),
    ) as client:
        with pytest.raises(ValueError):
            await client.chat(messages=[], category="TARGET", template_version="invalid", top_p=0.0)
    assert ledger.intents == []


@pytest.mark.parametrize("echo", [None, "muse-spark-wrong"])
async def test_missing_or_mismatched_spark_model_echo_is_unknown_charge(echo):
    body = chat_body()
    if echo is None:
        del body["model"]
    else:
        body["model"] = echo
    result, ledger = await run_chat(lambda request: httpx.Response(200, json=body))
    assert result.outcome == "INVALID" and len(ledger.intents) == 1
    assert ledger.finishes[0][1].kind == "UNKNOWN"
    assert ledger.finishes[0][1].reason == "INVALID_RESPONSE"


async def test_target_cannot_assert_a_count_without_confirmed_spark_model():
    from io import BytesIO

    from counterforge_contracts.versions import TargetOutcome
    from counterforge_meta.transport import MetaTransport
    from counterforge_targets.meta_spark import MetaSparkTarget
    from PIL import Image

    body = chat_body()
    del body["model"]
    image = BytesIO()
    Image.new("RGB", (3, 2)).save(image, "PNG")
    ledger = Ledger()
    async with MetaTransport(
        api_key="fixture-key",
        accounting=ledger,
        prices=prices(),
        http_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    ) as wire:
        result = await MetaSparkTarget(transport=wire, expected_count=5).evaluate(
            image.getvalue(), "Count containers", {}
        )
    assert result.parsed_answer is None and result.status == TargetOutcome.UNKNOWN
    assert ledger.finishes[0][1].kind == "UNKNOWN"
