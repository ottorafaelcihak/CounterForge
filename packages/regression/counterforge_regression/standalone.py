"""Vetted portable runner: standard library only, copied verbatim into exports."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any


class ExecutionError(RuntimeError):
    """No interpretable target observation was produced."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_count(raw_text: str) -> int | None:
    text = raw_text.strip()
    if re.fullmatch(r"-?(?:0|[1-9][0-9]{0,9})", text):
        return int(text)
    try:
        parsed: object = json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return None
    if isinstance(parsed, dict) and set(parsed) == {"count"} and type(parsed["count"]) is int:
        return int(parsed["count"])
    return None


def safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and not path.is_absolute()
        and all(p not in {"", ".", ".."} for p in name.split("/"))
        and bool(re.fullmatch(r"[A-Za-z0-9_./-]+", name))
    )


def verify(root: Path) -> dict[str, Any]:
    try:
        expected = {}
        for line in (root / "sha256sums.txt").read_text().splitlines():
            digest, name = line.split("  ")
            if (
                not safe_name(name)
                or name in expected
                or name == "sha256sums.txt"
                or not re.fullmatch("[0-9a-f]{64}", digest)
            ):
                raise ValueError()
            expected[name] = digest
        actual = set()
        for path in root.rglob("*"):
            if any(p in {"__pycache__", ".pytest_cache"} for p in path.relative_to(root).parts):
                continue
            if path.is_symlink():
                raise ValueError()
            if path.is_file():
                actual.add(path.relative_to(root).as_posix())
        if actual != set(expected) | {"sha256sums.txt"}:
            raise ValueError()
        for name, digest in expected.items():
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
                raise ValueError()
        manifest: dict[str, Any] = json.loads(
            (root / "manifest.json").read_text(), object_pairs_hook=_unique_object
        )
        if manifest["format_version"] != "1.0.0":
            raise ValueError()
        if manifest["sha256"] != {k: v for k, v in expected.items() if k != "manifest.json"}:
            raise ValueError()
        if any(not safe_name(v) or v not in expected for v in manifest["files"].values()):
            raise ValueError()
        if type(manifest["expected"]["value"]) is not int:
            raise ValueError()
        return manifest
    except (OSError, ValueError, KeyError, TypeError):
        raise ExecutionError(
            "Bundle integrity or format verification failed before invocation"
        ) from None


def live_target(manifest: dict[str, Any], image: bytes) -> str:
    model = manifest["model"]
    if (
        model["adapter"] != "META_SPARK"
        or model["model_id"] != "muse-spark-1.3"
        or manifest["provenance"] != "REAL_PROVIDER"
    ):
        raise ExecutionError("Live invocation requires a known real-provider target")
    options = dict(model["sampling_options"])
    if set(options) - {"temperature", "top_p", "max_completion_tokens", "max_output_tokens"} or (
        "max_completion_tokens" in options and "max_output_tokens" in options
    ):
        raise ExecutionError("Unsupported sampling options")
    tokens = options.pop("max_output_tokens", options.pop("max_completion_tokens", 128))
    if (
        type(tokens) is not int
        or not 1 <= tokens <= 4096
        or not 1 <= len(manifest["question"]) <= 2000
    ):
        raise ExecutionError("Invalid invocation bounds")
    for key, maximum in [("temperature", 2), ("top_p", 1)]:
        if key in options and options[key] is None:
            del options[key]
        if key in options and (
            type(options[key]) not in (float, int)
            or not 0 <= options[key] <= maximum
            or (key == "top_p" and options[key] == 0)
        ):
            raise ExecutionError("Invalid sampling options")
    api_key = os.environ.get("MODEL_API_KEY")
    if not api_key:
        raise ExecutionError("MODEL_API_KEY is required for explicitly authorized live invocation")
    payload = {
        "model": model["model_id"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": manifest["question"]},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + base64.b64encode(image).decode()
                        },
                    },
                ],
            }
        ],
        "max_completion_tokens": tokens,
        **options,
    }
    request = urllib.request.Request(
        "https://api.meta.ai/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    print(
        "LIVE authorized invocation: call_bound=1 timeout_seconds=45 token_bound=" + str(tokens),
        flush=True,
    )
    try:
        # Disable environment proxies and redirects so credentials never follow a private endpoint.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(
                self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
            ) -> None:
                return None

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=45) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError()
        reply = json.loads(data)
        usage = reply.get("usage")
        counts = {
            k: usage[k]
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(usage, dict) and type(usage.get(k)) is int and usage[k] >= 0
        }
        print(
            "LIVE usage: calls=1 tokens="
            + json.dumps(counts)
            + "; monetary_cost=unknown; no local billing estimate"
        )
        if (
            reply.get("model") != model["model_id"]
            or reply["choices"][0].get("finish_reason") != "stop"
        ):
            raise ValueError()
        message = reply["choices"][0]["message"]
        if message.get("refusal") or not isinstance(message.get("content"), str):
            raise ValueError()
        return str(message["content"])
    except (OSError, ValueError, KeyError, IndexError, TypeError, urllib.error.URLError):
        raise ExecutionError(
            "Provider transport/refusal/response error; attempted_calls=1, charge may be unknown; no automatic retry"
        ) from None


def run(root: Path, mode: str, fixture_count: str | None = None) -> None:
    manifest = verify(root)
    if mode == "replay":
        observations = json.loads((root / manifest["files"]["target_responses"]).read_text())
        fresh = [x for x in observations if x["phase"] == "CONFIRMATION"]
        if len(fresh) != 3 or any(x["outcome"] not in {"CORRECT", "INCORRECT"} for x in fresh):
            raise ExecutionError("Recorded observation evidence is incomplete")
        print(
            "OFFLINE RECORDED REPLAY provenance="
            + manifest["provenance"]
            + " recorded_counts="
            + str([x["parsed_count"] for x in fresh])
            + "; live_calls=0 live_tokens=0 live_cost=0; historic billing is not measured by replay",
            flush=True,
        )
        raw = fresh[0]["raw_text"]
    elif mode == "fixture":
        if manifest["provenance"] != "LOCAL_FIXTURE" or fixture_count is None:
            raise ExecutionError("Explicit fixture input requires LOCAL_FIXTURE provenance")
        print("LOCAL FIXTURE invocation; provider_calls=0 tokens=0 cost=0", flush=True)
        raw = fixture_count
    elif mode == "live":
        raw = live_target(manifest, (root / manifest["files"]["failing"]).read_bytes())
    else:
        raise ExecutionError("An explicit replay, fixture, or live invocation opt-in is required")
    actual = parse_count(raw)
    if actual is None:
        raise ExecutionError("Target output is unknown/refused/unparseable, not a count mismatch")
    expected = manifest["expected"]["value"]
    if actual != expected:
        raise AssertionError(
            f"expected={expected} actual={actual}; mode={mode} provenance={manifest['provenance']}"
        )
    print(f"expected={expected} actual={actual}; correctness assertion passed")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Checksum-verified correctness test; live requires explicit opt-in"
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--replay-recorded", action="store_true")
    modes.add_argument("--fixture-count")
    modes.add_argument("--live", action="store_true")
    args = parser.parse_args()
    mode = (
        "replay"
        if args.replay_recorded
        else "fixture"
        if args.fixture_count is not None
        else "live"
    )
    try:
        run(Path(__file__).resolve().parents[1], mode, args.fixture_count)
    except ExecutionError as exc:
        print("EXECUTION ERROR: " + str(exc), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
