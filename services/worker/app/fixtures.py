"""Original CC0 development seed and labeled SDK wire fixtures, never live evidence."""

from __future__ import annotations

import base64
import json
from hashlib import sha256
from pathlib import Path

import httpx

FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "phase03"
RECTANGLES = [
    (40, 60, 96, 176),
    (128, 60, 184, 176),
    (216, 60, 272, 176),
    (304, 60, 360, 176),
    (392, 60, 448, 176),
]


def seed_bytes() -> bytes:
    body = (FIXTURE_ROOT / "five-blue-containers.png").read_bytes()
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text())
    if sha256(body).hexdigest() != manifest["normalized_sha256"]:
        raise ValueError("Development fixture checksum mismatch")
    return body


def require_seed(body: bytes) -> None:
    if sha256(body).digest() != sha256(seed_bytes()).digest():
        raise ValueError("Fixture mode supports only the original development seed")


def sam_events() -> list[dict[str, object]]:
    text = "".join(
        f"<0f>{i}<|box;x1={x1};y1={y1};x2={x2 - 1};y2={y2 - 1};w=480;h=240|>"
        "<|mask;x=0;y=0;data=116,56,!!!!!(y:Whj]K7|>\n"
        for i, (x1, y1, x2, y2) in enumerate(RECTANGLES[:4])
    )
    lane = {"item_id": "msg-fixture", "output_index": 0, "content_index": 0}
    return [
        {"type": "response.output_text.delta", **lane, "delta": text},
        {"type": "response.output_text.done", **lane, "text": text},
        {
            "type": "response.completed",
            "response": {"id": "fixture-sam", "status": "completed", "model": "sam-3.1"},
        },
    ]


def mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        headers = {"x-request-id": "synthetic-fixture"}
        if request.url.path == "/v1/responses":
            payload = json.loads(body)
            media = payload["input"][0]["content"][1]["image_url"]
            require_seed(base64.b64decode(media.split(",", 1)[1], validate=True))
            stream = "".join("data: " + json.dumps(event) + "\n\n" for event in sam_events())
            return httpx.Response(
                200, headers={**headers, "content-type": "text/event-stream"}, content=stream
            )
        if request.url.path == "/v1/chat/completions":
            payload = json.loads(body)
            content = payload["messages"][-1]["content"]
            if isinstance(content, list):
                media = content[1]["image_url"]["url"]
                require_seed(base64.b64decode(media.split(",", 1)[1], validate=True))
                reply = "5"
            else:
                # Planner validation still runs in SparkPlanner; this is synthetic wire data.
                reply = '{"family":"brightness_shift","strength":0.2,"hypothesis":"Synthetic fixture proposal","prompt":"Adjust only background brightness; preserve every protected object."}'
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "id": "synthetic-chat",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "muse-spark-1.3",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": reply},
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
            )
        if request.url.path == "/v1/images/edits" and seed_bytes() in body:
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "created": 0,
                    "data": [{"b64_json": base64.b64encode(seed_bytes()).decode()}],
                },
            )
        raise ValueError("Unknown synthetic fixture request")

    return httpx.MockTransport(handler)
