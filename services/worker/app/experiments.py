"""One durable, fenced experiment stage per ID-only queue delivery.

No SQL transaction spans provider I/O. Every attempt uses the Phase03 execution
port; CPU candidate creation retains Phase04's unique-image slot accounting.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any, cast
from uuid import UUID, uuid4

from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.providers import TargetResult
from counterforge_contracts.requests import CreateMutation
from counterforge_contracts.versions import ErrorCode
from counterforge_contracts.vtc import Limits, VisualTestContract
from counterforge_meta.spark import SparkPlanner
from counterforge_meta.transport import MetaTransport
from counterforge_meta.usage import CallIntent, Charge, PriceBook, semantic_digest
from counterforge_targets.fixture import FixtureTarget
from counterforge_targets.meta_spark import MetaSparkTarget, decode_reply
from sqlalchemy import func, select

from services.api.app.accounting import ACTIVE_STATES, Budget, money
from services.api.app.errors import ServiceError
from services.api.app.experiments import (
    canonical_confirmation,
    event,
    release_confirmation_holds,
    reserve_confirmation_batch,
    transition,
)
from services.api.app.provider_accounting import ExperimentAccounting
from services.api.app.service import Service, fingerprint

from .fixtures import mock_transport, require_seed

LEASE = timedelta(seconds=30)


def classify_confirmation(outcomes: list[str]) -> str:
    if len(outcomes) != 3 or any(v not in {"CORRECT", "INCORRECT"} for v in outcomes):
        return "REVIEW_REQUIRED"
    wrong = outcomes.count("INCORRECT")
    return "STABLE" if wrong == 3 else "INTERMITTENT" if wrong else "NOT_REPRODUCED"


def experiment_prices(snapshot: dict[str, Any]) -> PriceBook:
    p = snapshot["pricing"]
    return PriceBook(p["revision"], p["source_url"], p["checked_at"], p["models"])


def owned(s: Service, job: dict[str, Any]) -> Any:
    row = s.row(s.t.experiments, job["id"], lock=True)
    s.project(row["project_id"])
    if (
        row["runner_token"] != job["runner_token"]
        or row["delivery_no"] != job["delivery_no"]
        or row["lease_expires_at"] is None
        or row["lease_expires_at"] <= Budget(s.uow)._now()
    ):
        raise ServiceError(ErrorCode.INVALID_STATE, "Experiment runner fence expired.")
    return row


def pause_review(s: Service, eid: UUID, reason: str) -> None:
    row = s.experiment(eid)
    cp = {
        **row["checkpoint_json"],
        "resume_state": row["state"]
        if row["state"] in ACTIVE_STATES
        else row["checkpoint_json"].get("resume_state", "BASELINE_VALIDATING"),
    }
    s.db.execute(
        s.t.experiments.update().where(s.t.experiments.c.id == eid).values(checkpoint_json=cp)
    )
    transition(s, eid, "PAUSED", reason)


def save_checkpoint(s: Service, eid: UUID, **values: Any) -> None:
    row = s.experiment(eid)
    s.db.execute(
        s.t.experiments.update()
        .where(s.t.experiments.c.id == eid)
        .values(checkpoint_json={**row["checkpoint_json"], **values})
    )


def batch_fits(s: Service, e: Any, count: int, prices: PriceBook) -> str | None:
    calls, _ = Budget(s.uow)._pending(e["id"])
    if e["calls_consumed"] + calls + count > e["max_target_calls"]:
        return "CONFIRMATION_TARGET_CALL_CAP"
    target = e["config_snapshot"]["target"]
    tokens = target["inference_options"].get(
        "max_output_tokens", target["inference_options"].get("max_completion_tokens", 128)
    )
    upper = (
        Decimal(0)
        if s.settings.provider_mode == "FIXTURE"
        else prices.upper("muse-spark-1.3", tokens)
    )
    if money(e["spent_usd"]) + money(e["reserved_usd"]) + money(upper) * count > money(
        e["max_estimated_usd"], ceiling=False
    ):
        return "CONFIRMATION_DOLLAR_CAP"
    if (Budget(s.uow)._now() - e["started_at"]).total_seconds() + count * 45 >= e[
        "max_runtime_seconds"
    ]:
        return "CONFIRMATION_RUNTIME_CAP"
    return None


def claim(factory: Any, settings: Any, eid: UUID) -> dict[str, Any] | None:
    from services.api.app.experiments import require_provider_mode

    with factory() as uow:
        s = Service(uow, settings)
        e = s.row(s.t.experiments, eid, lock=True)
        s.project(e["project_id"])
        if (
            not e["dispatch_pending"]
            or e["runner_token"] is not None
            or e["state"] not in ACTIVE_STATES
        ):
            return None
        try:
            require_provider_mode(s, e)
        except ServiceError:
            pause_review(s, eid, "PROVIDER_MODE_MISMATCH")
            s.db.execute(
                s.t.experiments.update()
                .where(s.t.experiments.c.id == eid)
                .values(dispatch_pending=False)
            )
            uow.commit()
            return None
        try:
            Budget(uow)._require_runnable(e)
        except ServiceError:
            transition(s, eid, "BUDGET_EXHAUSTED", "RUNTIME_EXPIRED")
            s.db.execute(
                s.t.experiments.update()
                .where(s.t.experiments.c.id == eid)
                .values(dispatch_pending=False)
            )
            uow.commit()
            return None
        token = uuid4()
        row = (
            s.db.execute(
                s.t.experiments.update()
                .where(s.t.experiments.c.id == eid)
                .values(
                    runner_token=token,
                    delivery_no=s.t.experiments.c.delivery_no + 1,
                    heartbeat_at=func.now(),
                    lease_expires_at=func.now() + LEASE,
                    dispatch_pending=False,
                    checkpoint_json={**e["checkpoint_json"], "resume_state": e["state"]},
                )
                .returning(s.t.experiments)
            )
            .mappings()
            .one()
        )
        result = dict(row)
        uow.commit()
        return result


async def heartbeat(factory: Any, settings: Any, job: dict[str, Any]) -> None:
    while True:
        await asyncio.sleep(5)
        with factory() as uow:
            s = Service(uow, settings)
            owned(s, job)
            s.db.execute(
                s.t.experiments.update()
                .where(s.t.experiments.c.id == job["id"])
                .values(heartbeat_at=func.now(), lease_expires_at=func.now() + LEASE)
            )
            uow.commit()


def accounting(
    factory: Any,
    settings: Any,
    job: dict[str, Any],
    purpose: str,
    mid: UUID | None = None,
    invocation_sha256: str | None = None,
) -> ExperimentAccounting:
    return ExperimentAccounting(
        factory,
        settings,
        experiment_id=job["id"],
        token=job["runner_token"],
        delivery=job["delivery_no"],
        purpose=purpose,
        mutation_id=mid,
        invocation_sha256=invocation_sha256,
    )


async def evaluate_target(transport: Any, target: Any, image: bytes, expected: int) -> TargetResult:
    """Adapter seam; fixture callers can supply explicit replies without live sockets."""
    if target["adapter_key"] == "LOCAL_FIXTURE":
        return await FixtureTarget(
            {sha256(image).hexdigest(): str(expected)}, expected_count=expected
        ).evaluate(image, target["prompt_template"], target["inference_options"])
    return await MetaSparkTarget(transport=transport, expected_count=expected).evaluate(
        image, target["prompt_template"], target["inference_options"]
    )


def target_invocation(snap: dict[str, Any], image_id: UUID, image: bytes) -> dict[str, Any]:
    target = snap["target"]
    return {
        "image_asset_id": str(image_id),
        "image_sha256": sha256(image).hexdigest(),
        "prompt": target["prompt_template"],
        "prompt_sha256": sha256(target["prompt_template"].encode()).hexdigest(),
        "target": target,
        "adapter_version": snap["adapter_version"],
        "verifier_version": snap["verifier_version"],
        "pricing_revision": snap["pricing_revision"],
    }


async def target_stage(
    factory: Any,
    settings: Any,
    job: dict[str, Any],
    phase: str,
    attempt: int,
    mid: UUID | None = None,
    fid: UUID | None = None,
) -> None:
    with factory() as uow:
        s = Service(uow, settings)
        e = owned(s, job)
        Budget(uow)._require_runnable(e)
        snap = e["config_snapshot"]
        target, expected = snap["target"], snap["contract"]["expected"]["value"]
        image_id = UUID(snap["seed"]["image_asset_id"])
        if mid:
            candidate = s.require_valid_candidate(mid)
            image_id = candidate.image_asset_id
            if image_id is None:
                raise ValueError("MISSING_IMAGE")
        image = s.artifacts.read_verified(image_id)
        if settings.provider_mode == "FIXTURE":
            require_seed(s.artifacts.read_verified(UUID(snap["seed"]["image_asset_id"])))
        invocation = target_invocation(snap, image_id, image)
        jid = s.finding(fid)["confirmation_job_id"] if fid else None
        uow.commit()
    prices = experiment_prices(snap)
    port = accounting(factory, settings, job, f"{phase}_{attempt}", mid, fingerprint(invocation))
    prefix = f"{job['id']}:{phase}_{attempt}:{mid or 'seed'}:"
    with factory() as uow:
        usage_table = uow.tables.usage_events
        prior = (
            uow.connection.execute(
                select(usage_table)
                .where(
                    usage_table.c.operation_key.startswith(prefix, autoescape=True),
                    usage_table.c.dispatched_at.is_not(None),
                )
                .order_by(usage_table.c.created_at.desc())
            )
            .mappings()
            .first()
        )
    if prior is not None:
        if prior["metadata_json"].get("invocation_sha256") != fingerprint(invocation):
            raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Retained target invocation differs.")
        if not prior["raw_response_asset_id"] or prior["status"] != "SETTLED":
            raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Prior target attempt needs resolution.")
        import json

        with factory() as uow:
            s = Service(uow, settings)
            raw = json.loads(s.artifacts.read_verified(prior["raw_response_asset_id"]))
        # MetaTransport retains a versioned response envelope; the local fixture
        # ticket stores its normalized wire directly. Decode the actual reply.
        reply = raw.get("response", raw) if isinstance(raw, dict) else raw
        text, parsed, outcome = decode_reply(reply, prior["outcome"], expected)
        options = target["inference_options"]
        if target["adapter_key"] == "META_SPARK":
            token_key = (
                "max_output_tokens" if "max_output_tokens" in options else "max_completion_tokens"
            )
            options = {**options, token_key: options.get(token_key, 128)}
        result = TargetResult(
            raw_text=text,
            parsed_answer=parsed,
            status=outcome,
            model_id=target["model_id"],
            version_claim="provider-managed"
            if target["adapter_key"] == "META_SPARK"
            else "fixture-v1",
            options=options,
            latency_ms=prior["latency_ms"] or 0,
            request_id=prior["provider_request_id"],
            raw_asset_sha256=sha256(text.encode()).hexdigest(),
        )
        raw_id = prior["raw_response_asset_id"]
    elif target["adapter_key"] == "LOCAL_FIXTURE":
        if settings.provider_mode != "FIXTURE":
            raise ValueError("LOCAL_FIXTURE_REQUIRES_FIXTURE_MODE")
        intent = CallIntent(
            "LOCAL_FIXTURE", "TARGET", semantic_digest(invocation), prices.revision, Decimal(0), 1
        )
        ticket = await port.reserve(intent)
        if not await ticket.claim():
            raise ServiceError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Provider lease unavailable.")
        try:
            result = await evaluate_target(None, target, image, expected)
            metadata = await ticket.finish(
                "COMPLETED",
                Charge("ACTUAL", Decimal(0), True, "VERIFIED_USAGE"),
                {},
                {"choices": [{"message": {"content": result.raw_text}}]},
            )
        finally:
            ticket.release()
        raw_id = UUID(metadata.raw_asset_key)
    else:
        if target["adapter_key"] != "META_SPARK" or target["model_id"] != "muse-spark-1.3":
            raise ValueError("UNSUPPORTED_TARGET")
        async with MetaTransport(
            api_key="synthetic-fixture-key"
            if port.synthetic
            else settings.model_api_key.get_secret_value(),
            accounting=port,
            prices=prices,
            http_transport=mock_transport() if port.synthetic else None,
            deadline_seconds=45,
        ) as transport:
            result = await evaluate_target(transport, target, image, expected)
        raw_id = None
    with factory() as uow:
        s = Service(uow, settings)
        e = owned(s, job)
        usage = (
            s.db.execute(
                select(s.t.usage_events)
                .where(
                    s.t.usage_events.c.experiment_id == job["id"],
                    s.t.usage_events.c.operation_key.startswith(prefix, autoescape=True),
                    s.t.usage_events.c.dispatched_at.is_not(None),
                )
                .order_by(s.t.usage_events.c.created_at.desc())
            )
            .mappings()
            .first()
        )
        if prior is not None:
            usage = prior
        if usage is None:
            raise ValueError("MISSING_ACCOUNTING")
        # A target outcome is retained even when a control request arrived in flight.
        obs = s.db.scalar(
            s.t.model_observations.insert()
            .values(
                experiment_id=e["id"],
                target_config_id=e["target_config_id"],
                seed_id=UUID(snap["seed"]["id"]) if mid is None else None,
                mutation_id=mid,
                phase=phase,
                attempt_no=attempt,
                request_fingerprint=fingerprint(invocation),
                raw_output_asset_id=raw_id or usage["raw_response_asset_id"],
                parsed_json={"count": result.parsed_answer},
                outcome=result.status,
                latency_ms=result.latency_ms,
                provider_request_id=result.request_id,
                model_version_observed=result.version_claim,
                sampling_config=result.options,
                invocation_json=invocation,
                operation_key=usage["operation_key"],
                confirmation_job_id=jid,
            )
            .returning(s.t.model_observations.c.id)
        )
        event(
            s,
            e["id"],
            "OBSERVATION_RECORDED",
            {
                "observation_id": str(obs),
                "phase": phase,
                "attempt": attempt,
                "outcome": result.status,
            },
        )
        # Update evidence/classification without overwriting a requested stop.
        stopped = e["state"] not in ACTIVE_STATES
        if phase == "SEED_BASELINE":
            if result.status == "INCORRECT" and not stopped:
                transition(s, e["id"], "SEED_INVALID_FOR_SEARCH", "BASELINE_INCORRECT")
            elif result.status in {"UNKNOWN", "PROVIDER_ERROR"} and not stopped:
                pause_review(s, e["id"], "BASELINE_REVIEW_REQUIRED")
            elif attempt == 3 and not stopped:
                outcomes = list(
                    s.db.scalars(
                        select(s.t.model_observations.c.outcome).where(
                            s.t.model_observations.c.experiment_id == e["id"],
                            s.t.model_observations.c.phase == "SEED_BASELINE",
                        )
                    )
                )
                if outcomes == ["CORRECT"] * 3:
                    transition(s, e["id"], "READY")
        elif phase == "SCREENING":
            state = (
                "TESTED_CORRECT"
                if result.status == "CORRECT"
                else "SUSPECTED_FAILURE"
                if result.status == "INCORRECT"
                else "EVALUATION_UNKNOWN"
            )
            s.db.execute(
                s.t.mutations.update().where(s.t.mutations.c.id == mid).values(state=state)
            )
            if result.status == "INCORRECT":
                finding_id = s.db.scalar(
                    s.t.findings.insert()
                    .values(experiment_id=e["id"], mutation_id=mid, status="SUSPECTED")
                    .returning(s.t.findings.c.id)
                )
                try:
                    canonical_confirmation(s, cast(UUID, finding_id))
                except ServiceError:
                    s.db.execute(
                        s.t.findings.update()
                        .where(s.t.findings.c.id == finding_id)
                        .values(status="REVIEW_REQUIRED")
                    )
                    if not stopped:
                        pause_review(s, e["id"], "FINDING_VALIDITY_REVIEW_REQUIRED")
                    stopped = True
                event(
                    s,
                    e["id"],
                    "FINDING_SUSPECTED",
                    {"finding_id": str(finding_id), "mutation_id": str(mid)},
                )
                if not stopped:
                    transition(s, e["id"], "CONFIRMING")
            elif result.status != "CORRECT" and not stopped:
                pause_review(s, e["id"], "SCREENING_REVIEW_REQUIRED")
        else:
            outcomes = list(
                s.db.scalars(
                    select(s.t.model_observations.c.outcome)
                    .where(s.t.model_observations.c.confirmation_job_id == jid)
                    .order_by(s.t.model_observations.c.attempt_no)
                )
            )
            status = classify_confirmation(outcomes)
            try:
                s.require_valid_candidate(cast(UUID, mid))
            except ServiceError:
                status = "REVIEW_REQUIRED"
            s.db.execute(
                s.t.findings.update()
                .where(s.t.findings.c.id == fid)
                .values(
                    status=status
                    if len(outcomes) == 3 or result.status not in {"CORRECT", "INCORRECT"}
                    else "SUSPECTED",
                    confirmation_attempts=len(outcomes),
                    confirmation_failures=outcomes.count("INCORRECT"),
                    updated_at=func.now(),
                )
            )
            if len(outcomes) == 3:
                s.db.execute(
                    s.t.mutations.update()
                    .where(s.t.mutations.c.id == mid)
                    .values(state=status, updated_at=func.now())
                )
                s.db.execute(
                    s.t.background_jobs.update()
                    .where(s.t.background_jobs.c.id == jid)
                    .values(
                        state="SUCCEEDED" if status != "REVIEW_REQUIRED" else "REVIEW_REQUIRED",
                        finished_at=func.now(),
                    )
                )
                event(
                    s,
                    e["id"],
                    "FINDING_CLASSIFIED",
                    {
                        "finding_id": str(fid),
                        "status": status,
                        "incorrect": outcomes.count("INCORRECT"),
                        "attempts": len(outcomes),
                    },
                )
                if not stopped:
                    transition(s, e["id"], "EVALUATING")
            if result.status not in {"CORRECT", "INCORRECT"} and not stopped:
                pause_review(s, e["id"], "CONFIRMATION_REVIEW_REQUIRED")
        uow.commit()


async def plan_stage(factory: Any, settings: Any, job: dict[str, Any]) -> None:
    snap = job["config_snapshot"]
    contract = VisualTestContract.model_validate(snap["contract"])
    prices = experiment_prices(snap)
    with factory() as uow:
        s = Service(uow, settings)
        e = owned(s, job)
        if settings.provider_mode == "FIXTURE":
            require_seed(s.artifacts.read_verified(UUID(snap["seed"]["image_asset_id"])))
        calls, slots = Budget(uow)._pending(e["id"])
        if (
            e["candidates_created"] + slots >= e["max_candidates"]
            or e["calls_consumed"] + calls >= e["max_target_calls"]
        ):
            transition(s, e["id"], "BUDGET_EXHAUSTED", "SEARCH_CAP_REACHED")
            uow.commit()
            return
        # Remaining quantities may fall below start-request minima; these are
        # planner context only. Original DB ceilings remain dispatch authority.
        remaining = Limits.model_validate(snap["limits"]).model_copy(
            update={
                "max_candidates": e["max_candidates"] - e["candidates_created"] - slots,
                "max_target_calls": e["max_target_calls"] - e["calls_consumed"] - calls,
                "max_estimated_usd": max(
                    0, float(e["max_estimated_usd"] - e["spent_usd"] - e["reserved_usd"])
                ),
                "max_runtime_seconds": max(
                    0,
                    int(
                        e["max_runtime_seconds"]
                        - (Budget(uow)._now() - e["started_at"]).total_seconds()
                    ),
                ),
            }
        )
    if job["mode"] == "RANDOM_BASELINE":
        from .random_strategy import RANDOM_REVISION, random_plan

        strategy = snap.get("strategy")
        if not strategy or strategy.get("revision") != RANDOM_REVISION:
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED,
                "Random search requires a recorded strategy seed and revision.",
            )
        plans = [
            random_plan(
                strategy["random_seed"],
                contract.allowed_mutations,
                float(prices.upper("muse-image-1.0")),
            )
        ]
        evidence = {
            "author": "RANDOM_BASELINE",
            "synthetic": settings.provider_mode == "FIXTURE",
            "random_seed": strategy["random_seed"],
            "strength_range": strategy["strength_range"],
        }
    else:
        port = accounting(factory, settings, job, "PLANNER")
        async with MetaTransport(
            api_key="synthetic-fixture-key"
            if port.synthetic
            else settings.model_api_key.get_secret_value(),
            accounting=port,
            prices=prices,
            http_transport=mock_transport() if port.synthetic else None,
            deadline_seconds=45,
        ) as transport:
            planner = SparkPlanner(transport=transport)
            plans = await planner.plan(contract, [], remaining)
            evidence = dict(planner.last_evidence)
    with factory() as uow:
        s = Service(uow, settings)
        e = owned(s, job)
        if e["state"] not in ACTIVE_STATES:
            save_checkpoint(
                s, e["id"], plans=[p.model_dump(mode="json") for p in plans], plan_done=True
            )
        else:
            save_checkpoint(
                s, e["id"], plans=[p.model_dump(mode="json") for p in plans], plan_done=True
            )
            transition(s, e["id"], "MUTATING")
        usage = (
            s.db.execute(
                select(s.t.usage_events)
                .where(
                    s.t.usage_events.c.experiment_id == e["id"],
                    s.t.usage_events.c.category == "PLANNER",
                )
                .order_by(s.t.usage_events.c.created_at.desc())
            )
            .mappings()
            .first()
        )
        for index, plan in enumerate(plans):
            retained_plan_id = s.db.scalar(
                s.t.mutation_plans.insert()
                .values(
                    experiment_id=e["id"],
                    planner_model=evidence["author"],
                    strategy="RANDOM" if e["mode"] == "RANDOM_BASELINE" else "META_ADAPTIVE",
                    strategy_revision="random-initial-v2"
                    if e["mode"] == "RANDOM_BASELINE"
                    else "spark-initial-v1",
                    hypothesis=s.candidates.clean(plan.hypothesis),
                    plan_json=plan.model_dump(mode="json"),
                    evidence_json=evidence,
                    planner_response_asset_id=usage["raw_response_asset_id"] if usage else None,
                )
                .returning(s.t.mutation_plans.c.id)
            )
            if index == 0:
                save_checkpoint(s, e["id"], source_plan_id=str(retained_plan_id))
        event(
            s,
            e["id"],
            "PLAN_RECORDED",
            {
                "strategy_revision": "spark-initial-v1"
                if e["mode"] == "ADAPTIVE"
                else "random-initial-v2",
                "author": evidence["author"],
            },
        )
        uow.commit()


async def preparation(
    factory: Any, settings: Any, job: dict[str, Any], kind: str, request: dict[str, Any], key: str
) -> UUID:
    from .jobs import execute_job

    with factory() as uow:
        s = Service(uow, settings)
        e = owned(s, job)
        Budget(uow)._require_runnable(e)
        uow.background_jobs.max_pending_jobs = settings.max_pending_jobs
        jid = uow.background_jobs.enqueue(
            project_id=e["project_id"],
            experiment_id=e["id"],
            kind=kind,
            request_json=request,
            idempotency_key=key,
        )
        uow.commit()
    await execute_job(factory, settings, jid)
    with factory() as uow:
        s = Service(uow, settings)
        owned(s, job)
        prepared = s.job(jid)
        if prepared["state"] != "SUCCEEDED":
            # SAM is supporting evidence: retain a completed uncertain result,
            # checkpoint it once, and leave the mandatory human gates unresolved.
            # A lost result/ambiguous dispatch has no such bound evidence.
            result = prepared["result_json"] or {}
            latest = (
                s.candidates.latest(UUID(request["mutation_id"]), required=False)
                if kind == "MUTATION_SAM"
                else None
            )
            sam = latest["evidence_json"].get("sam") if latest else None
            if not (
                kind == "MUTATION_SAM"
                and prepared["state"] == "REVIEW_REQUIRED"
                and result.get("outcome") in {"INCOMPLETE", "REFUSED", "PROVIDER_ERROR"}
                and sam
                and sam["asset_id"] in result.get("evidence_asset_ids", [])
            ):
                raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Preparation requires review.")
    return cast(UUID, jid)


async def stage(factory: Any, settings: Any, job: dict[str, Any]) -> None:
    eid, state, cp = job["id"], job["state"], job["checkpoint_json"]
    if settings.provider_mode == "DISABLED":
        raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Providers are disabled.")
    if state == "BASELINE_VALIDATING":
        with factory() as uow:
            outcomes = list(
                uow.connection.scalars(
                    select(uow.tables.model_observations.c.outcome).where(
                        uow.tables.model_observations.c.experiment_id == eid,
                        uow.tables.model_observations.c.phase == "SEED_BASELINE",
                    )
                )
            )
            s = Service(uow, settings)
            owned(s, job)
            if outcomes and any(v != "CORRECT" for v in outcomes):
                if "INCORRECT" in outcomes:
                    transition(s, eid, "SEED_INVALID_FOR_SEARCH", "BASELINE_INCORRECT")
                else:
                    pause_review(s, eid, "BASELINE_REVIEW_REQUIRED")
                uow.commit()
                return
            if len(outcomes) == 3:
                transition(s, eid, "READY")
                uow.commit()
                return
        await target_stage(factory, settings, job, "SEED_BASELINE", len(outcomes) + 1)
    elif state in {"READY", "PLANNING"}:
        if cp.get("plan_done"):
            with factory() as uow:
                s = Service(uow, settings)
                owned(s, job)
                transition(s, eid, "MUTATING")
                uow.commit()
        else:
            with factory() as uow:
                s = Service(uow, settings)
                owned(s, job)
                transition(s, eid, "PLANNING")
                uow.commit()
            await plan_stage(factory, settings, job)
    elif state == "MUTATING":
        if cp.get("generated"):
            with factory() as uow:
                s = Service(uow, settings)
                owned(s, job)
                transition(s, eid, "VALIDATING")
                uow.commit()
            return
        plans = cp.get("plans", [])
        if not plans:
            raise ServiceError(ErrorCode.REVIEW_REQUIRED, "No retained plan.")
        plan = MutationPlan.model_validate(plans[0])
        snap = job["config_snapshot"]
        background = None
        limits = {
            "max_calls": 3,
            "max_estimated_usd": float(job["max_estimated_usd"]),
            "max_runtime_seconds": job["max_runtime_seconds"],
        }
        if plan.requires_muse_image:
            from counterforge_mutations.registry import approve_plan

            approved = approve_plan(VisualTestContract.model_validate(snap["contract"]), plan)
            background = await preparation(
                factory,
                settings,
                job,
                "SCENE",
                {
                    "contract_id": str(job["contract_id"]),
                    "snapshot": snap,
                    "snapshot_sha256": fingerprint(snap),
                    "source_sha256": snap["seed"]["image_sha256"],
                    "plan_json": approved.plan_json,
                    "limits": limits,
                },
                f"{eid}:scene:0",
            )
        with factory() as uow:
            s = Service(uow, settings)
            e = owned(s, job)
            Budget(uow)._require_runnable(e)
            candidate = s.candidates.create(
                eid,
                CreateMutation(
                    plan=plan, rng_seed=int(eid.int % (2**31)), background_job_id=background
                ),
                "pipeline:0",
                source_plan_id=UUID(cp["source_plan_id"]) if cp.get("source_plan_id") else None,
                automated=True,
            )
            save_checkpoint(s, eid, generated=True, mutation_id=str(candidate.id))
            event(s, eid, "CANDIDATE_GENERATED", {"mutation_id": str(candidate.id)})
            transition(s, eid, "VALIDATING")
            uow.commit()
    elif state == "VALIDATING":
        mid = UUID(cp["mutation_id"]) if cp.get("mutation_id") else None
        if mid and not cp.get("sam_done"):
            with factory() as uow:
                s = Service(uow, settings)
                owned(s, job)
                candidate = s.candidates.row(mid)
                source_sha = candidate["evidence_json"]["asset_hashes"]["candidate"]
            await preparation(
                factory,
                settings,
                job,
                "MUTATION_SAM",
                {
                    "mutation_id": str(mid),
                    "source_sha256": source_sha,
                    "limits": {
                        "max_calls": 3,
                        "max_estimated_usd": float(job["max_estimated_usd"]),
                        "max_runtime_seconds": job["max_runtime_seconds"],
                    },
                },
                f"{eid}:sam:0",
            )
        with factory() as uow:
            s = Service(uow, settings)
            current = owned(s, job)
            save_checkpoint(s, eid, sam_done=True)
            if current["state"] in ACTIVE_STATES:
                transition(s, eid, "EVALUATING")
            uow.commit()
    elif state in {"EVALUATING", "CONFIRMING"}:
        with factory() as uow:
            s = Service(uow, settings)
            e = owned(s, job)
            f = (
                s.db.execute(
                    select(s.t.findings)
                    .where(
                        s.t.findings.c.experiment_id == eid, s.t.findings.c.status == "SUSPECTED"
                    )
                    .order_by(s.t.findings.c.created_at)
                )
                .mappings()
                .first()
            )
            if f:
                s.require_valid_candidate(f["mutation_id"])
                canonical_confirmation(s, f["id"])
                prices = experiment_prices(e["config_snapshot"])
                batch = e["checkpoint_json"].get("confirmation_batch")
                reason = None if batch else batch_fits(s, e, 3 - f["confirmation_attempts"], prices)
                if reason:
                    transition(s, eid, "BUDGET_EXHAUSTED", reason)
                    uow.commit()
                    return
                target = e["config_snapshot"]["target"]
                tokens = target["inference_options"].get(
                    "max_output_tokens",
                    target["inference_options"].get("max_completion_tokens", 128),
                )
                upper = (
                    Decimal(0)
                    if settings.provider_mode == "FIXTURE"
                    else prices.upper("muse-spark-1.3", tokens)
                )
                reserve_confirmation_batch(s, e, f["id"], 3 - f["confirmation_attempts"], upper)
                transition(s, eid, "CONFIRMING")
                uow.commit()
                phase, attempt, mid, fid = (
                    "CONFIRMATION",
                    f["confirmation_attempts"] + 1,
                    f["mutation_id"],
                    f["id"],
                )
            else:
                rows = (
                    s.db.execute(
                        select(s.t.mutations)
                        .where(s.t.mutations.c.experiment_id == eid)
                        .order_by(s.t.mutations.c.created_at)
                    )
                    .mappings()
                    .all()
                )
                review = bool(
                    s.db.scalar(
                        select(func.count())
                        .select_from(s.t.findings)
                        .where(
                            s.t.findings.c.experiment_id == eid,
                            s.t.findings.c.status == "REVIEW_REQUIRED",
                        )
                    )
                )
                selected = None
                for row in rows:
                    observed = s.db.scalar(
                        select(func.count())
                        .select_from(s.t.model_observations)
                        .where(
                            s.t.model_observations.c.mutation_id == row["id"],
                            s.t.model_observations.c.phase == "SCREENING",
                        )
                    )
                    if observed:
                        continue
                    latest = s.candidates.latest(row["id"], required=False)
                    if latest is None or latest["verdict"] == "REVIEW_REQUIRED":
                        review = True
                        continue
                    if latest["verdict"] == "INVALID":
                        continue
                    s.require_valid_candidate(row["id"])
                    selected = row["id"]
                    break
                if selected is None:
                    if review:
                        pause_review(s, eid, "CANDIDATE_HUMAN_REVIEW_REQUIRED")
                    else:
                        transition(s, eid, "COMPLETED", "BOUNDED_PLAN_COMPLETE")
                    uow.commit()
                    return
                phase, attempt, mid, fid = "SCREENING", 1, selected, None
                transition(s, eid, "EVALUATING")
                uow.commit()
        await target_stage(factory, settings, job, phase, attempt, mid, fid)


async def execute_experiment(factory: Any, settings: Any, eid: UUID) -> None:
    job = claim(factory, settings, eid)
    if job is None:
        return
    pulse = asyncio.create_task(heartbeat(factory, settings, job))
    try:
        with factory() as uow:
            remaining = (
                job["max_runtime_seconds"]
                - (Budget(uow)._now() - job["started_at"]).total_seconds()
            )
        async with asyncio.timeout(remaining):
            await stage(factory, settings, job)
    except Exception as exc:
        with factory() as uow:
            s = Service(uow, settings)
            e = owned(s, job)
            pending = (
                s.db.execute(
                    select(s.t.usage_events).where(
                        s.t.usage_events.c.experiment_id == eid,
                        s.t.usage_events.c.status == "RESERVED",
                    )
                )
                .mappings()
                .all()
            )
            for usage in pending:
                if usage["dispatched_at"] is None:
                    Budget(uow).void_operation(usage["operation_key"])
                else:
                    Budget(uow).mark_unknown_charge(
                        usage["operation_key"], "EXPERIMENT_STAGE_FAILED"
                    )
            if e["state"] not in {"PAUSE_REQUESTED", "CANCEL_REQUESTED"}:
                if isinstance(exc, TimeoutError) or (
                    isinstance(exc, ServiceError) and exc.code == ErrorCode.BUDGET_EXCEEDED
                ):
                    transition(s, eid, "BUDGET_EXHAUSTED", "STAGE_BUDGET_EXCEEDED")
                else:
                    pause_review(s, eid, "EXPERIMENT_STAGE_REVIEW_REQUIRED")
            uow.commit()
    finally:
        pulse.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await pulse
        with factory() as uow:
            s = Service(uow, settings)
            e = owned(s, job)
            state = e["state"]
            if state in {"PAUSE_REQUESTED", "CANCEL_REQUESTED"}:
                transition(
                    s, eid, "PAUSED" if state == "PAUSE_REQUESTED" else "CANCELLED", "OWNER_REQUEST"
                )
                state = "PAUSED" if state == "PAUSE_REQUESTED" else "CANCELLED"
            if state not in ACTIVE_STATES:
                release_confirmation_holds(s, eid)
            s.db.execute(
                s.t.experiments.update()
                .where(s.t.experiments.c.id == eid)
                .values(
                    runner_token=None,
                    lease_expires_at=None,
                    heartbeat_at=func.now(),
                    dispatch_pending=state in ACTIVE_STATES,
                )
            )
            uow.commit()
        if settings.enqueue_jobs and state in ACTIVE_STATES:
            from .queue import enqueue_experiments

            enqueue_experiments(settings, [eid])


def recover_experiments(factory: Any, settings: Any) -> list[UUID]:
    """Recover PostgreSQL intent after lost Redis; never replay ambiguous paid sends."""
    with factory() as uow:
        s = Service(uow, settings)
        t, usage = s.t.experiments, s.t.usage_events
        rows = (
            s.db.execute(
                select(t)
                .join(s.t.projects, s.t.projects.c.id == t.c.project_id)
                .where(
                    s.t.projects.c.owner_label == settings.owner_label,
                    t.c.runner_token.is_not(None),
                    t.c.lease_expires_at <= func.now(),
                )
                .with_for_update(of=t, skip_locked=True)
            )
            .mappings()
            .all()
        )
        for e in rows:
            events = (
                s.db.execute(select(usage).where(usage.c.experiment_id == e["id"])).mappings().all()
            )
            ambiguous = False
            for v in events:
                if v["status"] == "RESERVED":
                    if v["dispatched_at"] is None:
                        Budget(uow).void_operation(v["operation_key"])
                    else:
                        Budget(uow).mark_unknown_charge(
                            v["operation_key"], "EXPERIMENT_LEASE_EXPIRED"
                        )
                        ambiguous = True
                elif v["dispatched_at"] is not None and v["status"] != "VOID":
                    if v["category"] == "TARGET":
                        recorded = s.db.scalar(
                            select(s.t.model_observations.c.id).where(
                                s.t.model_observations.c.operation_key == v["operation_key"]
                            )
                        )
                        if not recorded and not (
                            v["status"] == "SETTLED"
                            and v["raw_response_asset_id"]
                            and v["metadata_json"].get("finish_sha256")
                        ):
                            ambiguous = True
                    elif v["category"] == "PLANNER":
                        if not e["checkpoint_json"].get("plan_done"):
                            ambiguous = True
                    elif v["background_job_id"]:
                        prep = s.job(v["background_job_id"])
                        if prep["state"] != "SUCCEEDED":
                            ambiguous = True
                    else:
                        ambiguous = True
            if ambiguous:
                release_confirmation_holds(s, e["id"])
                save_checkpoint(s, e["id"], unresolved_result=True)
            current = s.experiment(e["id"])
            if e["state"] in {"CANCEL_REQUESTED", "PAUSE_REQUESTED"}:
                transition(
                    s,
                    e["id"],
                    "CANCELLED" if e["state"] == "CANCEL_REQUESTED" else "PAUSED",
                    "OWNER_REQUEST",
                )
            elif ambiguous or current["state"] == "PAUSED":
                pause_review(s, e["id"], "RECOVERY_PROVIDER_OUTCOME_REVIEW_REQUIRED")
            s.db.execute(
                t.update()
                .where(t.c.id == e["id"])
                .values(
                    runner_token=None,
                    lease_expires_at=None,
                    dispatch_pending=not ambiguous
                    and s.experiment(e["id"])["state"] in ACTIVE_STATES,
                )
            )
        ids = list(
            s.db.scalars(
                select(t.c.id)
                .join(s.t.projects, s.t.projects.c.id == t.c.project_id)
                .where(
                    t.c.dispatch_pending,
                    t.c.runner_token.is_(None),
                    t.c.state.in_(sorted(ACTIVE_STATES)),
                    s.t.projects.c.owner_label == settings.owner_label,
                )
            )
        )
        uow.commit()
        return ids
