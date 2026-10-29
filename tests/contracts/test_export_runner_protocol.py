"""Static portable protocol agrees with product parsing/settings without network."""

import json
from types import SimpleNamespace

import pytest
from counterforge_regression import standalone
from counterforge_verification.counting import parse_count

from tests.unit.test_regression_bundle import example_manifest


@pytest.mark.parametrize(
    "raw",
    [
        "5",
        " 4 ",
        '{"count":5}',
        '{"count":true}',
        '{"count":4,"count":5}',
        "5 objects",
        "05",
        "-1",
        "1.0",
        '{"count":1234567890123}',
    ],
)
def test_parser_parity(raw):
    assert standalone.parse_count(raw) == parse_count(raw)


def reply(monkeypatch, wire):
    sent = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, limit):
            return json.dumps(wire).encode()

    def open_request(request, timeout):
        sent.append(json.loads(request.data))
        return Response()

    monkeypatch.setenv("MODEL_API_KEY", "fixture-not-a-real-secret")
    monkeypatch.setattr(
        standalone.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_request)
    )
    return sent


def manifest():
    m = example_manifest().model_dump(mode="json")
    m["provenance"] = "REAL_PROVIDER"
    m["model"] = {
        "adapter": "META_SPARK",
        "model_id": "muse-spark-1.3",
        "revision": "provider-managed",
        "sampling_options": {"temperature": 0.0, "top_p": None, "max_output_tokens": 64},
    }
    return m


def test_live_transport_nullable_defaults_and_truthful_usage(monkeypatch, capsys):
    sent = reply(
        monkeypatch,
        {
            "model": "muse-spark-1.3",
            "choices": [{"finish_reason": "stop", "message": {"content": "5"}}],
        },
    )
    assert standalone.live_target(manifest(), b"png") == "5"
    assert sent[0]["temperature"] == 0.0 and "top_p" not in sent[0]
    assert sent[0]["max_completion_tokens"] == 64
    assert "monetary_cost=unknown" in capsys.readouterr().out


@pytest.mark.parametrize("reason", ["length", "content_filter", None])
def test_truncated_numeric_reply_is_execution_error(monkeypatch, reason):
    reply(
        monkeypatch,
        {
            "model": "muse-spark-1.3",
            "choices": [{"finish_reason": reason, "message": {"content": "4"}}],
        },
    )
    m = manifest()
    m["model"]["sampling_options"].pop("top_p")
    with pytest.raises(standalone.ExecutionError, match="response error"):
        standalone.live_target(m, b"png")


def test_transport_failure_is_execution_error_never_count_mismatch(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "fixture")

    def fail(*args, **kwargs):
        raise OSError("fixture timeout")

    monkeypatch.setattr(standalone.urllib.request, "build_opener", fail)
    m = manifest()
    m["model"]["sampling_options"].pop("top_p")
    with pytest.raises(standalone.ExecutionError, match="no automatic retry"):
        standalone.live_target(m, b"png")
