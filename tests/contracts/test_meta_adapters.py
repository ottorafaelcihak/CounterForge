"""Package adapters normalize provider data without application imports."""

import base64
import json
from io import BytesIO

import httpx
import pytest
from counterforge_contracts.providers import ProviderResult
from counterforge_contracts.vtc import Limits, VisualTestContract


def png():
    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (3, 2), "white").save(output, format="PNG")
    return output.getvalue()


class Transport:
    def __init__(self, content="", *, outcome="COMPLETED", data=None):
        from pathlib import Path

        from counterforge_meta.usage import PriceBook

        self.prices = PriceBook.load(Path(__file__).resolve().parents[2] / "pricing.toml")
        self.content, self.outcome, self.data = content, outcome, data
        self.calls = []

    async def chat(self, **kwargs):
        from counterforge_meta.transport import CallResponse

        self.calls.append(kwargs)
        return CallResponse(
            self.outcome,
            ProviderResult(provider="META", model="muse-spark-1.3", latency_ms=1),
            self.data or {"choices": [{"message": {"content": self.content}}]},
        )

    async def image(self, **kwargs):
        from counterforge_meta.transport import CallResponse

        self.calls.append(kwargs)
        return CallResponse(
            self.outcome,
            ProviderResult(provider="META", model="muse-image-1.0", latency_ms=1),
            self.data,
        )


async def test_planner_generated_strict_schema_and_independent_vtc_validation(vtc_payload):
    from counterforge_meta.spark import SparkPlanner

    proposal = {
        "family": "background_clutter",
        "strength": 0.3,
        "hypothesis": "Test clutter",
        "prompt": "Busy background",
    }
    transport = Transport(json.dumps(proposal))
    planner = SparkPlanner(transport=transport)
    contract = VisualTestContract.model_validate(vtc_payload)
    plans = await planner.plan(contract, [], Limits())
    assert transport.calls[0]["max_completion_tokens"] == 4096
    assert transport.calls[0]["reasoning_effort"] == "low"
    assert len(plans) == 1
    assert plans[0].family == "background_clutter"
    schema = transport.calls[0]["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert set(schema["schema"]["required"]) == {"family", "strength", "hypothesis", "prompt"}
    assert planner.last_evidence["author"] == "META_SPARK"
    assert len(planner.last_evidence["proposal_sha256"]) == 64


@pytest.mark.parametrize(
    "content,outcome",
    [
        ('{"family":"object_removal"}', "COMPLETED"),
        ('{"family":"brightness_shift","strength":2,"hypothesis":"x","prompt":"x"}', "COMPLETED"),
        ("not json", "COMPLETED"),
        ("", "REFUSED"),
        ("", "INCOMPLETE"),
    ],
)
async def test_planner_invalid_output_uses_labeled_bounded_fallback(vtc_payload, content, outcome):
    from counterforge_meta.spark import SparkPlanner

    planner = SparkPlanner(transport=Transport(content, outcome=outcome))
    contract = VisualTestContract.model_validate(vtc_payload)
    plans = await planner.plan(contract, [], Limits())
    assert len(plans) == 1
    plans[0].validate_for_contract(contract)
    assert plans[0].strength <= 0.2
    assert "Deterministic fallback" in plans[0].hypothesis
    assert planner.last_evidence["author"] == "DETERMINISTIC_FALLBACK"
    assert planner.last_evidence["template_version"]


async def test_planner_rejects_valid_schema_family_forbidden_by_vtc(vtc_payload):
    from counterforge_meta.spark import SparkPlanner

    vtc_payload["allowed_mutations"] = ["brightness_shift"]
    planner = SparkPlanner(
        transport=Transport(
            '{"family":"background_clutter","strength":0.2,"hypothesis":"x","prompt":"x"}'
        )
    )
    plans = await planner.plan(VisualTestContract.model_validate(vtc_payload), [], Limits())
    assert plans[0].family == "brightness_shift"
    assert planner.last_evidence["author"] == "DETERMINISTIC_FALLBACK"


@pytest.mark.parametrize(
    "raw,want",
    [
        ("5", 5),
        (" 5\n", 5),
        ('{"count":5}', 5),
        ('{"count":5,"x":1}', None),
        ('{"count":5,"count":6}', None),
        ("5 6", None),
        ("There are 5", None),
        ("5.0", None),
        ('{"count":true}', None),
        ("", None),
    ],
)
async def test_target_strict_count_and_honest_version(raw, want):
    from counterforge_targets.meta_spark import MetaSparkTarget

    result = await MetaSparkTarget(transport=Transport(raw), expected_count=5).evaluate(
        png(), "Count objects", {}
    )
    assert result.parsed_answer == want
    assert result.version_claim == "provider-managed"
    assert result.status == ("CORRECT" if want == 5 else "UNKNOWN")
    assert result.model_id == "muse-spark-1.3"


@pytest.mark.parametrize("outcome", ["REFUSED", "INCOMPLETE", "UNKNOWN"])
async def test_target_cutoff_or_refusal_never_claims_count(outcome):
    from counterforge_targets.meta_spark import MetaSparkTarget

    result = await MetaSparkTarget(
        transport=Transport("5", outcome=outcome), expected_count=5
    ).evaluate(png(), "Count objects", {})
    assert result.parsed_answer is None
    assert result.status == "UNKNOWN"


async def test_scene_decodes_bounded_png_base64():
    from counterforge_meta.image import SceneGenerator

    data = png()
    generator = SceneGenerator(
        transport=Transport(data={"data": [{"b64_json": base64.b64encode(data).decode()}]})
    )
    assert await generator.create_scene(data, "background only") == data


async def test_scene_default_url_policy_is_empty_and_never_downloads():
    from counterforge_meta.image import SceneError, SceneGenerator

    generator = SceneGenerator(
        transport=Transport(
            data={"data": [{"url": "https://generated.meta.invalid/signed?token=secret"}]}
        )
    )
    with pytest.raises(SceneError, match="IMAGE_URL_DENIED"):
        await generator.create_scene(png(), "background only")


async def test_scene_trusted_fixture_redirect_revalidated_and_no_auth():
    from counterforge_meta.image import SceneError, SceneGenerator, URLPolicy

    async def resolve(host):
        return ("93.184.216.34",)

    requests = []

    def handler(request):
        requests.append(request)
        assert "authorization" not in request.headers
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})
        return httpx.Response(200, content=png(), headers={"content-type": "image/png"})

    generator = SceneGenerator(
        transport=Transport(data={"data": [{"url": "https://fixture.example/ok"}]}),
        url_policy=URLPolicy(frozenset({"fixture.example"}), resolver=resolve),
        download_transport=httpx.MockTransport(handler),
    )
    assert await generator.create_scene(png(), "background only") == png()
    generator = SceneGenerator(
        transport=Transport(data={"data": [{"url": "https://fixture.example/redirect"}]}),
        url_policy=URLPolicy(frozenset({"fixture.example"}), resolver=resolve),
        download_transport=httpx.MockTransport(handler),
    )
    with pytest.raises(SceneError, match="IMAGE_URL_DENIED"):
        await generator.create_scene(png(), "background only")
    assert len(requests) == 2


@pytest.mark.parametrize(
    "payload", [{"data": []}, {"data": [{"b64_json": "bad"}]}, {"data": [{"b64_json": "eA=="}]}]
)
async def test_scene_invalid_media_is_fixed_error(payload):
    from counterforge_meta.image import SceneError, SceneGenerator

    with pytest.raises(SceneError):
        await SceneGenerator(transport=Transport(data=payload)).create_scene(
            png(), "background only"
        )


async def test_target_pinned_options_are_honored_and_effective_options_recorded():
    from counterforge_targets.meta_spark import MetaSparkTarget

    transport = Transport("5")
    pinned = {"temperature": 0.0, "top_p": 0.9, "max_output_tokens": 256}
    result = await MetaSparkTarget(transport=transport, expected_count=5).evaluate(
        png(), "Count objects", pinned
    )
    assert transport.calls[0]["temperature"] == 0.0
    assert transport.calls[0]["top_p"] == 0.9
    assert transport.calls[0]["max_completion_tokens"] == 256
    assert result.options == pinned


@pytest.mark.parametrize("content", [None, {"family": "brightness_shift"}])
async def test_planner_nonstring_content_still_has_bounded_fallback(vtc_payload, content):
    from counterforge_meta.spark import SparkPlanner

    planner = SparkPlanner(
        transport=Transport(data={"choices": [{"message": {"content": content}}]})
    )
    plans = await planner.plan(VisualTestContract.model_validate(vtc_payload), [], Limits())
    assert len(plans) == 1
    assert planner.last_evidence["author"] == "DETERMINISTIC_FALLBACK"


async def test_planner_context_omits_reviewer_and_asset_identifiers(vtc_payload):
    from counterforge_meta.spark import SparkPlanner

    vtc_payload["expected"]["reviewer"] = "Personal Reviewer Name"
    transport = Transport("invalid")
    await SparkPlanner(transport=transport).plan(
        VisualTestContract.model_validate(vtc_payload), [], Limits()
    )
    payload = transport.calls[0]["messages"][1]["content"]
    assert "Personal Reviewer Name" not in payload
    assert "mask_asset_ids" not in payload
    assert '"expected":5' in payload


async def test_planner_cost_estimate_uses_injected_central_tariff(vtc_payload):
    from pathlib import Path

    from counterforge_meta.spark import SparkPlanner
    from counterforge_meta.usage import PriceBook

    transport = Transport(
        '{"family":"background_clutter","strength":0.2,"hypothesis":"x","prompt":"x"}'
    )
    transport.prices = PriceBook.load(Path(__file__).resolve().parents[2] / "pricing.toml")
    transport.prices.models["muse-image-1.0"]["per_image"] = "0.02"
    plans = await SparkPlanner(transport=transport).plan(
        VisualTestContract.model_validate(vtc_payload), [], Limits()
    )
    assert plans[0].estimated_cost_usd == 0.022


async def test_target_accepts_api_default_null_top_p():
    from counterforge_contracts.providers import ProviderResult
    from counterforge_meta.transport import CallResponse
    from counterforge_targets.meta_spark import MetaSparkTarget

    class Transport:
        async def chat(self, **kwargs):
            return CallResponse(
                "COMPLETED",
                ProviderResult(provider="META", model="muse-spark-1.3", latency_ms=0),
                {"choices": [{"message": {"content": "5"}}]},
            )

    image = png()
    result = await MetaSparkTarget(transport=Transport(), expected_count=5).evaluate(
        image, "Count the objects.", {"temperature": 0.0, "top_p": None, "max_output_tokens": 64}
    )
    assert result.status == "CORRECT" and result.options["top_p"] is None


async def test_signed_download_url_never_appears_in_http_logs(caplog):
    import logging

    import httpx
    from counterforge_meta.image import SceneGenerator, URLPolicy

    async def resolve(host):
        return ("8.8.8.8",)

    caplog.set_level(logging.INFO, logger="httpx")
    generator = SceneGenerator(
        transport=None,
        url_policy=URLPolicy(frozenset({"images.example.test"}), resolve),
        download_transport=httpx.MockTransport(lambda request: httpx.Response(200, content=png())),
    )
    assert (
        await generator._download("https://images.example.test/image?token=PRIVATE_SIGNED_URL")
        == png()
    )
    assert "PRIVATE_SIGNED_URL" not in caplog.text
