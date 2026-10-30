"""Opt-in evaluation worker fence and serialized aggregate authorization.

Ordinary experiments are unaffected. Evaluation intent is attached transactionally
by the local coordinator before any delivery. No key is persisted in this envelope.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from counterforge_contracts.versions import ErrorCode
from sqlalchemy import select

from .errors import ServiceError
from .service import fingerprint

GLOBAL_EVALUATION_STOP_REASON = "GLOBAL_EVALUATION_ENVELOPE_EXPIRED"


class GlobalEvaluationExhausted(ServiceError):
    """Shared global envelope/runtime/authorization stopped the evaluation.

    Distinguishable from a per-arm local cap: arms censored this way were
    never matched under equal declared caps, so reports must withhold the
    matched comparison. ``stop_reason`` is the durable reason recorded on
    the experiment row.
    """

    stop_reason = GLOBAL_EVALUATION_STOP_REASON

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.BUDGET_EXCEEDED, message)


def envelope(experiment: Any) -> dict[str, Any] | None:
    value = experiment["config_snapshot"].get("evaluation")
    if value is None:
        return None
    if value.get("sha256") != fingerprint({k: v for k, v in value.items() if k != "sha256"}):
        raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Evaluation declaration integrity mismatch.")
    if str(experiment["id"]) not in value["experiment_ids"]:
        raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Experiment outside evaluation declaration.")
    return dict(value)


def _auth_peer_ids(budget: Any, value: dict[str, Any]) -> list[UUID] | None:
    """All experiment IDs sharing this LIVE authorization in the database.

    Returns None for fixture runs, which stay scoped to their own run.
    """
    auth = value.get("authorization_sha256")
    if not auth:
        return None
    table = budget.t.experiments
    ids = list(
        budget.db.scalars(
            select(table.c.id).where(
                table.c.config_snapshot["evaluation"]["authorization_sha256"].astext == auth
            )
        )
    )
    return ids or [UUID(i) for i in value["experiment_ids"]]


def _scope_started_at(budget: Any, value: dict[str, Any]) -> str:
    """Earliest run start in scope; a replayed run cannot reset the clock."""
    peer_ids = _auth_peer_ids(budget, value)
    if not peer_ids:
        return str(value["started_at"])
    table = budget.t.experiments
    starts = list(
        budget.db.scalars(
            select(table.c.config_snapshot["evaluation"]["started_at"].astext).where(
                table.c.id.in_(peer_ids)
            )
        )
    )
    known = [s for s in starts if s]
    return min(known) if known else str(value["started_at"])


def require_runtime(budget: Any, experiment: Any) -> None:
    value = envelope(experiment)
    if value and not complete_membership(budget, experiment):
        raise ServiceError(
            ErrorCode.REVIEW_REQUIRED, "Evaluation awaits complete immutable membership."
        )
    if (
        value
        and (
            budget._now() - datetime.fromisoformat(_scope_started_at(budget, value))
        ).total_seconds()
        >= value["max_runtime_seconds"]
    ):
        raise GlobalEvaluationExhausted("Global evaluation runtime expired.")
    if (
        value
        and value.get("authorization_expires_at")
        and budget._now() >= datetime.fromisoformat(value["authorization_expires_at"])
    ):
        raise GlobalEvaluationExhausted("Live evaluation authorization expired.")


def reserve_global(budget: Any, experiment: Any, upper: Decimal, count: int) -> None:
    value = envelope(experiment)
    if value is None:
        return
    # Every reservation, including IMAGE/SAM preparation and confirmation holds,
    # serializes on one run lock. Holds conservatively occupy future API slots.
    from sqlalchemy import func

    # Acquire the authorization lock before selecting any peer IDs or
    # usage: a peer list read first can miss a concurrently created run
    # sharing this authorization. Fixture runs lock on their own run key.
    lock_key = value.get("authorization_sha256") or value["run_sha256"]
    lock_id = int(lock_key[:15], 16)
    budget.db.execute(select(func.pg_advisory_xact_lock(lock_id)))
    # LIVE scope is the authorization, not one run: replaying the same
    # authorization in a new run must not regain the full paid envelope.
    # Fixture runs stay scoped to their own declaration.
    peer_ids = _auth_peer_ids(budget, value)
    scope_ids = peer_ids if peer_ids is not None else [UUID(i) for i in value["experiment_ids"]]
    require_runtime(budget, experiment)
    usage = budget.t.usage_events
    rows = (
        budget.db.execute(
            select(usage).where(
                usage.c.experiment_id.in_(scope_ids),
                usage.c.status != "VOID",
            )
        )
        .mappings()
        .all()
    )
    requests = sum(r["request_count"] for r in rows)
    spent = Decimal(0)
    for r in rows:
        if r["status"] == "SETTLED" and r["cost_kind"] == "ACTUAL":
            spent += r["actual_usd"] or Decimal(0)
        elif r["status"] == "SETTLED" and r["cost_kind"] == "UNBILLED":
            continue
        elif r["status"] == "SETTLED" and r["cost_kind"] == "ESTIMATED":
            spent += r["accounted_estimate_usd"]
        else:
            spent += r["estimated_max_usd"]
    if requests + count > value["max_api_calls"] or spent + upper > Decimal(value["max_usd"]):
        raise GlobalEvaluationExhausted("Global evaluation API/dollar authorization exhausted.")


def complete_membership(s: Any, experiment: Any) -> bool:
    value = envelope(experiment)
    if value is None:
        return True
    table = s.t.experiments
    peers = list(
        s.db.execute(
            select(table).where(table.c.id.in_([UUID(i) for i in value["experiment_ids"]]))
        ).mappings()
    )
    if len(peers) != len(value["experiment_ids"]):
        return False
    by_id = {str(e["id"]): e for e in peers}
    for peer in peers:
        if envelope(peer) != value:
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED, "Evaluation membership declaration differs."
            )
    for group in value["baseline_groups"]:
        if len(group) != 3 or len(set(group)) != 3:
            raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Evaluation matched group is incomplete.")
        rows = [by_id[i] for i in group]
        if {r["config_snapshot"]["evaluation_identity"]["strategy"] for r in rows} != {
            "random_det",
            "spark_det",
            "full_system",
        }:
            raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Evaluation arm identity mismatch.")
        common = []
        for row in rows:
            snap = row["config_snapshot"]
            common.append(
                dict(
                    seed=snap["seed"],
                    masks=snap["masks"],
                    target=snap["target"],
                    contract={
                        k: v for k, v in snap["contract"].items() if k != "allowed_mutations"
                    },
                    limits=snap["limits"],
                    provider_mode=snap["provider_mode"],
                    verifier_version=snap["verifier_version"],
                    policy_version=snap["policy_version"],
                )
            )
        if any(c != common[0] for c in common):
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED,
                "Matched evaluation seed/masks/target/validity/total caps differ.",
            )
    return True


def search_allowed(s: Any, experiment: Any) -> bool:
    """No planning/mutation may precede correctness of every matched baseline."""
    if not complete_membership(s, experiment):
        return False
    value = envelope(experiment)
    if value is None or experiment["state"] == "BASELINE_VALIDATING":
        return True
    group = next(g for g in value["baseline_groups"] if str(experiment["id"]) in g)
    observations = s.t.model_observations
    outcomes = {
        eid: list(
            s.db.scalars(
                select(observations.c.outcome).where(
                    observations.c.experiment_id == UUID(eid),
                    observations.c.phase == "SEED_BASELINE",
                )
            )
        )
        for eid in group
    }
    failures = any(any(o != "CORRECT" for o in rows) for rows in outcomes.values())
    for eid in group:
        e = s.experiment(UUID(eid))
        if len(outcomes[eid]) != 3 and e["state"] in {
            "PAUSED",
            "FAILED",
            "CANCELLED",
            "BUDGET_EXHAUSTED",
            "SEED_INVALID_FOR_SEARCH",
        }:
            failures = True
    if failures:
        from .experiments import transition

        for eid in group:
            e = s.experiment(UUID(eid))
            if e["runner_token"] is not None:
                # A running peer must finish accounting before a group decision.
                return False
        for eid in group:
            transition(s, UUID(eid), "SEED_INVALID_FOR_SEARCH", "MATCHED_BASELINE_NOT_ESTABLISHED")
            s.db.execute(
                s.t.experiments.update()
                .where(s.t.experiments.c.id == UUID(eid))
                .values(dispatch_pending=False)
            )
        return False
    return all(rows == ["CORRECT"] * 3 for rows in outcomes.values())
