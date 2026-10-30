"""Bounded allowlist; planner input is facts, never provider/media envelopes."""

import json
import math
import re
from typing import Any

from counterforge_contracts.history import PlannerHistory
from pydantic import ValidationError

from .usage import sanitize_raw

HISTORY_LIMIT = 10
PARAMETERS = frozenset(
    {
        "algorithm",
        "level",
        "brightness_numerator",
        "brightness_denominator",
        "rounding",
        "clipping",
        "prng",
        "blend_policy",
        "ring_width",
    }
)


def clean_text(value: object, limit: int = 500) -> str:
    if re.search(r"\b(?:sk|key)[_-][A-Za-z0-9_-]{8,}", str(value), re.I):
        return "[redacted]"
    return str(sanitize_raw({"text": str(value)[:2000]}).get("text", ""))[:limit]


def sanitized_history(
    records: list[dict[str, Any]], *, limit: int = HISTORY_LIMIT, max_bytes: int | None = 16384
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    safe = []
    for row in records[-limit:]:
        params = row.get("recipe_parameters", {})
        params = params if isinstance(params, dict) else {}
        params = {
            k: clean_text(v, 80) if isinstance(v, str) else v
            for k, v in params.items()
            if k in PARAMETERS
            and (isinstance(v, (str, bool)) or type(v) in (int, float) and math.isfinite(v))
        }
        try:
            item = PlannerHistory(
                hypothesis=clean_text(row.get("hypothesis", "")),
                family=row.get("family"),
                strength=row.get("strength"),
                recipe_parameters=params,
                validity=row.get("validity"),
                reason_codes=[clean_text(v, 100) for v in row.get("reason_codes", [])[:20]],
                target_outcomes=[
                    {k: v.get(k) for k in ("phase", "outcome")}
                    for v in row.get("target_outcomes", [])[:20]
                    if isinstance(v, dict)
                ],
                cost_usd=row.get("cost_usd", 0),
            )
        except (ValidationError, TypeError):
            continue
        safe.append(item.model_dump(mode="json"))
    while (
        max_bytes is not None and len(json.dumps(safe, separators=(",", ":")).encode()) > max_bytes
    ):
        safe.pop(0)
    return safe
