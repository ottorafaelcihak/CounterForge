"""Accounting port. Implementations own durable transactions, caps and idempotency."""

from __future__ import annotations

import json
import logging
import re
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, cast

from counterforge_contracts.providers import ProviderResult
from pydantic import JsonValue


def quiet_wire_logs() -> None:
    """Raw SDK bodies and signed download URLs are never application logs."""
    for name in ("openai", "openai._base_client", "httpx", "httpcore"):
        logging.getLogger(name).disabled = True


Category = Literal["PLANNER", "IMAGE", "SAM", "TARGET", "OTHER"]
Outcome = Literal[
    "COMPLETED",
    "REFUSED",
    "INCOMPLETE",
    "INVALID",
    "RATE_LIMITED",
    "PROVIDER_ERROR",
    "UNKNOWN",
    "NOT_DISPATCHED",
]
ChargeKind = Literal["ACTUAL", "ESTIMATED", "UNBILLED", "UNKNOWN"]
Reason = Literal[
    "VERIFIED_USAGE",
    "COMPLETED_IMAGES",
    "MISSING_USAGE",
    "CONNECT_FAILED",
    "UNBILLED_RATE_LIMIT",
    "AMBIGUOUS_DISPATCH",
    "CANCELLED",
    "INVALID_RESPONSE",
    "DEADLINE_BEFORE_SEND",
]


@dataclass(frozen=True)
class CallIntent:
    model: str
    category: Category
    semantic_sha256: str
    pricing_revision: str
    upper_bound_usd: Decimal
    attempt: int


@dataclass(frozen=True)
class Charge:
    kind: ChargeKind
    amount_usd: Decimal
    request_sent: bool
    reason: Reason

    def __post_init__(self) -> None:
        if not self.amount_usd.is_finite() or self.amount_usd < 0:
            raise ValueError("invalid charge")
        if self.kind == "UNBILLED" and self.amount_usd != 0:
            raise ValueError("unbilled amount must be zero")


class Ticket(Protocol):
    async def claim(self) -> bool:
        """Commit a durable dispatch claim before returning True. False never dispatches."""
        ...

    async def finish(
        self,
        outcome: Outcome,
        charge: Charge,
        metadata: dict[str, JsonValue],
        sanitized_raw: dict[str, JsonValue],
    ) -> ProviderResult:
        """Persist evidence and reconcile/quarantine charge; commit before returning."""
        ...


class Accounting(Protocol):
    async def reserve(self, intent: CallIntent) -> Ticket:
        """Enforce global job/call/cost caps and semantic-key conflicts, then commit."""
        ...


@dataclass(frozen=True)
class PriceBook:
    revision: str
    source_url: str
    checked_at: str
    models: dict[str, dict[str, object]]

    @classmethod
    def load(cls, path: Path | str) -> PriceBook:
        # Explicit path only: the parent deploys this central file with its bridge.
        body = Path(path).read_bytes()
        value = tomllib.loads(body.decode())
        return cls(
            f"{value['revision']}:{sha256(body).hexdigest()[:16]}",
            value["source_url"],
            value["checked_at"],
            value["models"],
        )

    def rate(self, model: str, key: str) -> Decimal:
        value = Decimal(str(self.models[model][key]))
        if not value.is_finite() or value < 0:
            raise ValueError("invalid price")
        return value

    def upper(self, model: str, max_completion_tokens: int = 4096) -> Decimal:
        if model == "muse-spark-1.3":
            maximum = int(self.rate(model, "max_completion_tokens"))
            if type(max_completion_tokens) is not int or not 1 <= max_completion_tokens <= maximum:
                raise ValueError("completion token bound exceeded")
            return (
                self.rate(model, "context_tokens") * self.rate(model, "input_per_million")
                + Decimal(max_completion_tokens) * self.rate(model, "output_per_million")
            ) / Decimal(1000000)
        return self.rate(model, "per_image") + self.rate(model, "upper_margin")

    def token_charge(
        self, model: str, usage: object, upper: Decimal, max_completion_tokens: int = 4096
    ) -> Charge:
        if isinstance(usage, dict):
            incoming, outgoing = usage.get("prompt_tokens"), usage.get("completion_tokens")
            details = usage.get("prompt_tokens_details")
            cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
            if (
                type(incoming) is int
                and type(outgoing) is int
                and type(cached) is int
                and 0 <= cached <= incoming <= int(self.rate(model, "context_tokens"))
                and 0
                <= outgoing
                <= min(max_completion_tokens, int(self.rate(model, "max_completion_tokens")))
            ):
                cost = (
                    Decimal(incoming - cached) * self.rate(model, "input_per_million")
                    + Decimal(cached) * self.rate(model, "cached_input_per_million")
                    + Decimal(outgoing) * self.rate(model, "output_per_million")
                ) / Decimal(1000000)
                return Charge("ACTUAL", cost, True, "VERIFIED_USAGE")
        return Charge("ESTIMATED", upper, True, "MISSING_USAGE")


def semantic_digest(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


# Raw evidence is deliberately a small tree, never an SDK object, header or exception.
_ALLOWED = frozenset(
    {
        "id",
        "model",
        "object",
        "created",
        "usage",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_tokens_details",
        "cached_tokens",
        "input_tokens",
        "output_tokens",
        "choices",
        "index",
        "finish_reason",
        "message",
        "role",
        "content",
        "refusal",
        "status",
        "type",
        "response",
        "output",
        "text",
        "data",
        "revised_prompt",
        "code",
        "outcome",
        "reason",
        "pricing_revision",
        "template_version",
        "proposal_sha256",
        "author",
        "fallback_reason",
        "raw_proposal",
        "max_completion_tokens",
        "reasoning_effort",
    }
)
_UNSAFE = re.compile(
    r"https?://|data:|bearer\s|(?:api[_-]?key|token|secret|password)\s*[:=]|[A-Za-z0-9+/]{80,}={0,2}",
    re.I,
)


def sanitize_raw(value: object, *, secrets: tuple[str, ...] = ()) -> dict[str, JsonValue]:
    def clean(item: object, depth: int = 0) -> JsonValue:
        if depth > 12:
            return None
        if isinstance(item, dict):
            return {
                str(k): clean(v, depth + 1) for k, v in list(item.items())[:128] if k in _ALLOWED
            }
        if isinstance(item, (list, tuple)):
            return [clean(v, depth + 1) for v in item[:128]]
        if isinstance(item, str):
            if _UNSAFE.search(item) or any(secret and secret in item for secret in secrets):
                return "[redacted]"
            return item[:8192]
        if item is None or type(item) in (int, bool):
            return cast(JsonValue, item)
        return None

    safe = clean(value)
    return safe if isinstance(safe, dict) else {}
