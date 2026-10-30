"""Read-only local API latency sampler. No model or provider calls.

Samples GET /api/v1/health/live (and owner-only /api/v1/health/ready when
OWNER_AUTH_TOKEN is present in the environment; the token travels only in
the Authorization header, never in a URL). Prints observed percentiles
against the 500 ms target from the technical specification. Observed
values describe this run and environment only; they are not a benchmark
guarantee.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

TARGET_P95_MS = 500.0


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("no samples")
    ordered = sorted(values)
    # nearest-rank, 1-based
    index = min(len(ordered), max(1, int(fraction * len(ordered) + 0.999999)))
    return ordered[index - 1]


def summarize(samples_ms: list[float]) -> dict[str, Any]:
    return {
        "samples": len(samples_ms),
        "min_ms": round(min(samples_ms), 2),
        "p50_ms": round(percentile(samples_ms, 0.50), 2),
        "p95_ms": round(percentile(samples_ms, 0.95), 2),
        "max_ms": round(max(samples_ms), 2),
        "mean_ms": round(statistics.fmean(samples_ms), 2),
        "target_p95_ms": TARGET_P95_MS,
        "p95_within_target": percentile(samples_ms, 0.95) <= TARGET_P95_MS,
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def validate_base_url(raw: str) -> str:
    """Loopback HTTP origin only: no userinfo, path, query or fragment."""
    parts = urlsplit(raw)
    if (
        parts.scheme != "http"
        or parts.hostname not in {"127.0.0.1", "::1", "localhost"}
        or parts.username
        or parts.password
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError("base URL must be a loopback HTTP origin without path/query")
    return raw.rstrip("/")


def sample(base_url: str, path: str, token: str, timeout: float) -> float:
    request = urllib.request.Request(base_url.rstrip("/") + path, method="GET")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    allowed = {200, 503} if path.endswith("/health/ready") else {200}
    started = time.perf_counter()
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            response.read()
            status = response.status
    except urllib.error.HTTPError as error:  # e.g. readiness 503: still a timed read
        error.read()
        status = error.code
    if status not in allowed:
        raise ValueError(f"unexpected HTTP status {status}")
    return (time.perf_counter() - started) * 1000.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--samples", type=int, default=50, help="bounded 1..200")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--project-id", default=None, help="optional owner project UUID")
    args = parser.parse_args(argv)
    if not 1 <= args.samples <= 200:
        parser.error("--samples must be between 1 and 200")
    if not 0 < args.timeout <= 30:
        parser.error("--timeout must be finite and at most 30 seconds")
    try:
        base_url = validate_base_url(args.base_url)
        project_id = str(UUID(args.project_id)) if args.project_id else None
    except ValueError as exc:
        parser.error(str(exc))
    token = os.environ.get("OWNER_AUTH_TOKEN", "")
    endpoints = ["/api/v1/health/live"]
    if token:
        endpoints.append("/api/v1/health/ready")
        endpoints.append("/api/v1/projects")
        if project_id:
            endpoints.append(f"/api/v1/projects/{project_id}/experiments?limit=5")
    report: dict[str, Any] = {
        "base_url": base_url,
        "model_calls": 0,
        "endpoints": {},
    }
    failed = False
    for path in endpoints:
        samples: list[float] = []
        for _ in range(args.samples):
            try:
                samples.append(sample(base_url, path, token, args.timeout))
            except Exception as exc:  # keep observed failures visible, not silent
                report["endpoints"][path] = {"error": type(exc).__name__, "samples": len(samples)}
                failed = True
                break
        else:
            report["endpoints"][path] = summarize(samples)
    print(json.dumps(report, indent=2, sort_keys=True))
    print(
        "Observed local read-only latency only; not a benchmark guarantee. "
        "No provider/model calls were made."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
