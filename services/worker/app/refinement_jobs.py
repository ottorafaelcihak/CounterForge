"""Fenced minimization checkpoints and separately accounted exact-input retests."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import UUID

from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.refinement import MinimizationResult, RetestComparison, RetestTarget
from counterforge_contracts.requests import CreateMutation, JobLimits
from counterforge_contracts.responses import JobResult
from counterforge_contracts.versions import ErrorCode
from counterforge_meta.transport import MetaTransport
from counterforge_meta.usage import CallIntent, Charge, PriceBook, semantic_digest
from sqlalchemy import func, select

from services.api.app.accounting import Budget
from services.api.app.errors import ServiceError
from services.api.app.experiments import require_provider_mode
from services.api.app.provider_accounting import Accounting
from services.api.app.refinements import (
    require_retest_provenance,
    require_target,
    target_snapshot,
    verified_export,
)
from services.api.app.service import Service, fingerprint

from . import experiments as search
from .jobs import fenced


def require_mode(settings: Any, target: Any) -> None:
    if settings.provider_mode == "DISABLED":
        raise ServiceError(ErrorCode.INVALID_STATE, "Providers are disabled.")
    if target["adapter_key"] == "LOCAL_FIXTURE":
        if settings.provider_mode != "FIXTURE" or not settings.allow_synthetic_fixtures:
            raise ServiceError(ErrorCode.INVALID_STATE, "Fixture target requires fixture mode.")
    elif (
        settings.provider_mode != "LIVE"
        or not settings.run_meta_live_tests
        or not settings.model_api_key.get_secret_value().strip()
    ):
        raise ServiceError(
            ErrorCode.INVALID_STATE,
            "Live target requires explicit live authorization and credentials.",
        )


def retest_input(s: Any, job: Any) -> tuple[bytes, Any, dict[str, Any]]:
    request = job["request_json"]
    ref, image, manifest = verified_export(s, UUID(request["export_id"]))
    target = require_target(s, UUID(request["target_config_id"]), job["project_id"])
    if (
        ref.bundle_sha256 != request["bundle_sha256"]
        or ref.manifest_sha256 != request["manifest_sha256"]
        or manifest.model_dump(mode="json") != request["manifest"]
        or sha256(image).hexdigest() != request["input_sha256"]
        or sha256(manifest.question.encode()).hexdigest() != request["prompt_sha256"]
        or target_snapshot(target) != request["target_snapshot"]
    ):
        raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Retained retest input or settings differ.")
    require_retest_provenance(manifest, target)
    require_mode(s.settings, target)
    return image, manifest, {**target_snapshot(target), "prompt_template": manifest.question}


class RetestAccounting(Accounting):
    def owned_job(self, uow: Any, *, fence: bool = True) -> Any:
        job = super().owned_job(uow, fence=fence)
        if fence:
            retest_input(Service(uow, self.settings), job)
        return job


def comparison(s: Any, job: Any, reason: str | None = None) -> RetestComparison:
    r = job["request_json"]
    m = r["manifest"]
    rows = (
        s.db.execute(
            select(s.t.model_observations)
            .where(s.t.model_observations.c.retest_job_id == job["id"])
            .order_by(s.t.model_observations.c.attempt_no)
        )
        .mappings()
        .all()
    )
    outcomes = [o["outcome"] for o in rows]
    correct, wrong = outcomes.count("CORRECT"), outcomes.count("INCORRECT")
    classification = (
        "INCONCLUSIVE"
        if len(rows) != 3 or correct + wrong != 3 or reason
        else "FIXED"
        if correct == 3
        else "PERSISTENT"
        if wrong == 3
        else "INTERMITTENT"
    )
    target = r["target_snapshot"]
    a = RetestTarget(
        adapter_key=m["model"]["adapter"],
        model_id=m["model"]["model_id"],
        configured_revision=m["model"]["revision"],
        inference_options=m["model"]["sampling_options"],
    )
    historical = (
        s.db.execute(
            select(s.t.model_observations.c.model_version_observed).where(
                s.t.model_observations.c.mutation_id == s.finding(job["finding_id"])["mutation_id"],
                s.t.model_observations.c.phase.in_(["SCREENING", "CONFIRMATION"]),
            )
        )
        .scalars()
        .all()
    )
    a.observed_revisions = sorted(set(x for x in historical if x))
    b = RetestTarget(
        adapter_key=target["adapter_key"],
        model_id=target["model_id"],
        configured_revision=target["model_revision"],
        inference_options=target["inference_options"],
        observed_revisions=sorted(
            set(o["model_version_observed"] for o in rows if o["model_version_observed"])
        ),
    )
    warnings = ["PROVIDER_VERSION_NOT_VERIFIED"]
    if target["adapter_key"] == "LOCAL_FIXTURE":
        warnings.append("LOCAL_FIXTURE_SIMULATED")
    if (
        a.adapter_key == b.adapter_key
        and a.model_id == b.model_id
        and a.configured_revision == b.configured_revision
        and a.inference_options == b.inference_options
    ):
        warnings.append("UNCHANGED_TARGET_CONFIG")
    return RetestComparison(
        export_id=UUID(r["export_id"]),
        source_finding_id=job["finding_id"],
        target_config_id=UUID(r["target_config_id"]),
        target_a=a,
        target_b=b,
        bundle_sha256=r["bundle_sha256"],
        manifest_sha256=r["manifest_sha256"],
        input_sha256=r["input_sha256"],
        prompt_sha256=r["prompt_sha256"],
        expected_count=m["expected"]["value"],
        completed_attempts=len(rows),
        correct=correct,
        incorrect=wrong,
        unknown=outcomes.count("UNKNOWN"),
        errors=outcomes.count("PROVIDER_ERROR"),
        interpretable_attempts=correct + wrong,
        classification=classification,
        observation_ids=[o["id"] for o in rows],
        warnings=warnings,
        stop_reason=reason or ("INCOMPLETE_ATTEMPTS" if len(rows) < 3 else None),
    )


def complete(
    s: Any, job: Any, result: JobResult, *, review: bool = False, pending_review: bool = False
) -> None:
    s.db.execute(
        s.t.background_jobs.update()
        .where(s.t.background_jobs.c.id == job["id"])
        .values(
            state="REVIEW_REQUIRED" if review else "SUCCEEDED",
            result_json=result.model_dump(mode="json"),
            finished_at=None if pending_review else func.now(),
            updated_at=func.now(),
            lease_expires_at=None,
            claim_token=None,
            error_code="REVIEW_REQUIRED" if review else None,
        )
    )


async def execute_retest(factory: Any, settings: Any, job: dict[str, Any]) -> None:
    reason = None
    synthetic = (
        settings.provider_mode == "FIXTURE"
        or job["request_json"]["manifest"]["provenance"] == "LOCAL_FIXTURE"
    )
    try:
        limits = JobLimits.model_validate(job["request_json"]["limits"])
        with factory() as uow:
            remaining = (
                limits.max_runtime_seconds
                - (Budget(uow)._now() - job["started_at"]).total_seconds()
            )
        async with asyncio.timeout(max(0.001, remaining)):
            for attempt in range(1, 4):
                with factory() as uow:
                    s = Service(uow, settings)
                    if fenced(uow, job) is None:
                        return
                    image, manifest, target = retest_input(s, job)
                    source = s.finding(job["finding_id"])
                    e = s.experiment(source["experiment_id"])
                    mutation = s.candidates.row(source["mutation_id"])
                    invocation = {
                        "image_sha256": sha256(image).hexdigest(),
                        "prompt": manifest.question,
                        "prompt_sha256": sha256(manifest.question.encode()).hexdigest(),
                        "target": target,
                        "export_id": str(manifest.export_job_id),
                        "bundle_sha256": job["request_json"]["bundle_sha256"],
                    }
                    if settings.provider_mode == "FIXTURE":
                        search.require_seed(
                            s.artifacts.read_verified(
                                UUID(e["config_snapshot"]["seed"]["image_asset_id"])
                            )
                        )
                    uow.commit()
                port = RetestAccounting(
                    factory,
                    settings,
                    job_id=job["id"],
                    claim_token=job["claim_token"],
                    delivery=job["delivery_no"],
                    purpose=f"RETEST_{attempt}",
                    synthetic=synthetic,
                )
                prices = PriceBook.load(settings.pricing_path)
                raw_id = None
                if target["adapter_key"] == "LOCAL_FIXTURE":
                    ticket = await port.reserve(
                        CallIntent(
                            "LOCAL_FIXTURE",
                            "TARGET",
                            semantic_digest(invocation),
                            prices.revision,
                            Decimal(0),
                            1,
                        )
                    )
                    if not await ticket.claim():
                        raise ServiceError(
                            ErrorCode.DEPENDENCY_UNAVAILABLE, "Provider lease unavailable."
                        )
                    try:
                        result = await search.evaluate_target(
                            None, target, image, manifest.expected.value
                        )
                        raw = await ticket.finish(
                            "COMPLETED",
                            Charge("ACTUAL", Decimal(0), True, "VERIFIED_USAGE"),
                            {},
                            {"choices": [{"message": {"content": result.raw_text}}]},
                        )
                        raw_id = UUID(raw.raw_asset_key)
                    finally:
                        ticket.release()
                else:
                    async with MetaTransport(
                        api_key=settings.model_api_key.get_secret_value(),
                        accounting=port,
                        prices=prices,
                        deadline_seconds=min(45, remaining),
                    ) as transport:
                        result = await search.evaluate_target(
                            transport, target, image, manifest.expected.value
                        )
                with factory() as uow:
                    if fenced(uow, job) is None:
                        return
                    s = Service(uow, settings)
                    usage = (
                        s.db.execute(
                            select(s.t.usage_events)
                            .where(
                                s.t.usage_events.c.background_job_id == job["id"],
                                s.t.usage_events.c.operation_key.startswith(
                                    f"{job['id']}:{job['delivery_no']}:RETEST_{attempt}:",
                                    autoescape=True,
                                ),
                                s.t.usage_events.c.dispatched_at.is_not(None),
                            )
                            .order_by(s.t.usage_events.c.created_at.desc())
                        )
                        .mappings()
                        .first()
                    )
                    if usage is None:
                        raise ServiceError(
                            ErrorCode.REVIEW_REQUIRED, "Missing retained accounting."
                        )
                    s.db.execute(
                        s.t.model_observations.insert().values(
                            experiment_id=e["id"],
                            target_config_id=UUID(job["request_json"]["target_config_id"]),
                            mutation_id=mutation["id"],
                            phase="RETEST",
                            retest_job_id=job["id"],
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
                        )
                    )
                    cp = comparison(s, job)
                    s.db.execute(
                        s.t.background_jobs.update()
                        .where(s.t.background_jobs.c.id == job["id"])
                        .values(
                            result_json=JobResult(
                                outcome="INCOMPLETE",
                                synthetic=synthetic,
                                retest=cp,
                            ).model_dump(mode="json")
                        )
                    )
                    uow.commit()
                    if usage["status"] == "UNKNOWN_CHARGE":
                        raise ServiceError(
                            ErrorCode.REVIEW_REQUIRED, "Unknown charge requires review."
                        )
    except Exception as exc:
        reason = (
            str(exc.code)
            if isinstance(exc, ServiceError)
            else "RUNTIME_EXPIRED"
            if isinstance(exc, TimeoutError)
            else "RETEST_EXECUTION_FAILED"
        )
    with factory() as uow:
        if fenced(uow, job) is None:
            return
        s = Service(uow, settings)
        reconcile(s, job)
        final_comparison = comparison(s, job, reason)
        complete(
            s,
            job,
            JobResult(
                outcome="COMPLETED" if reason is None else "INCOMPLETE",
                synthetic=synthetic,
                retest=final_comparison,
                diagnostics=[reason] if reason else [],
            ),
            review=bool(reason),
        )
        uow.commit()


def reconcile(s: Any, job: Any) -> None:
    u = s.t.usage_events
    for event in (
        s.db.execute(select(u).where(u.c.background_job_id == job["id"], u.c.status == "RESERVED"))
        .mappings()
        .all()
    ):
        if event["dispatched_at"] is None:
            Budget(s.uow).void_operation(event["operation_key"])
        else:
            Budget(s.uow).mark_unknown_charge(event["operation_key"], "FOLLOWUP_INTERRUPTED")


def next_level(recipe: dict[str, Any], best: int, boundary: int) -> int | None:
    algorithm = recipe["parameters"]["algorithm"]
    if algorithm == "brightness_integer_v1":
        return None if abs(best - boundary) <= 1 else (best + boundary) // 2
    if algorithm == "rectangles_v1":
        return None if best < 100 else (best // 100) * 100 - 1
    return None


def minimization_result(s: Any, job: Any, cp: dict[str, Any], stop: str) -> JobResult:
    r = job["request_json"]
    events = (
        s.db.execute(
            select(s.t.usage_events).where(s.t.usage_events.c.background_job_id == job["id"])
        )
        .mappings()
        .all()
    )
    dispatched = [event for event in events if event["dispatched_at"] is not None]
    usage = {
        **s.job_dto(s.job(job["id"])).usage.model_dump(mode="json"),
        "calls": sum(event["request_count"] for event in dispatched),
        "target_calls": sum(
            event["request_count"] for event in dispatched if event["category"] == "TARGET"
        ),
    }
    result = MinimizationResult(
        source_finding_id=UUID(r["source_finding_id"]),
        source_mutation_id=UUID(r["source_mutation_id"]),
        best_finding_id=UUID(cp["best_finding_id"]),
        best_mutation_id=UUID(cp["best_mutation_id"]),
        probe_limit=r["probe_limit"],
        probes_used=len(cp["probes"]),
        probes=cp["probes"],
        stop_reason=stop,
        usage=usage,
    )
    return JobResult(
        outcome="INCOMPLETE"
        if stop in {"REVIEW_REQUIRED", "PROVIDER_MODE_UNAVAILABLE"}
        else "COMPLETED",
        synthetic=s.settings.provider_mode == "FIXTURE"
        or s.experiment(job["experiment_id"])["config_snapshot"].get("provider_mode") == "FIXTURE",
        minimization=result,
    )


async def execute_minimize(factory: Any, settings: Any, job: dict[str, Any]) -> None:
    cp: dict[str, Any] = {}
    stop = "PROBE_LIMIT"
    try:
        while True:
            with factory() as uow:
                s = Service(uow, settings)
                e = search.owned(s, job)
                m = s.candidates.row(UUID(job["request_json"]["source_mutation_id"]))
                recipe = m["recipe_json"]
                current = s.job(job["id"])
                cp = current["checkpoint_json"] or {
                    "best_mutation_id": str(m["id"]),
                    "best_finding_id": str(job["finding_id"]),
                    "best_level": recipe["parameters"]["level"],
                    "boundary_level": 500 if recipe["family"] == "brightness_shift" else 0,
                    "probes": [],
                    "awaiting_review": False,
                }
                s.db.execute(
                    s.t.background_jobs.update()
                    .where(s.t.background_jobs.c.id == job["id"])
                    .values(checkpoint_json=cp)
                )
                try:
                    require_provider_mode(s, e)
                    require_mode(settings, e["config_snapshot"]["target"])
                except ServiceError:
                    stop = "PROVIDER_MODE_UNAVAILABLE"
                    uow.commit()
                    break
                source_evidence = s.finding_evidence(job["finding_id"])
                if not source_evidence.export_eligible:
                    raise ServiceError(
                        ErrorCode.REVIEW_REQUIRED, "Source finding is no longer eligible."
                    )
                probe = (
                    cp["probes"][-1]
                    if cp["probes"] and cp["probes"][-1]["outcome"] == "REVIEW_REQUIRED"
                    else None
                )
                if probe is None:
                    if len(cp["probes"]) >= job["request_json"]["probe_limit"]:
                        stop = "PROBE_LIMIT"
                        uow.commit()
                        break
                    level = next_level(recipe, cp["best_level"], cp["boundary_level"])
                    if level is None:
                        stop = (
                            "GENERATIVE_BACKGROUND"
                            if recipe["parameters"]["algorithm"] == "supplied_background_v1"
                            else "NO_REMOVABLE_ELEMENT"
                            if recipe["family"] == "background_clutter"
                            else "NO_SIMPLER_LEVEL"
                            if recipe["family"] == "brightness_shift"
                            else "UNSUPPORTED_FAMILY"
                        )
                        uow.commit()
                        break
                    reason = search.batch_fits(
                        s, e, 4, search.experiment_prices(e["config_snapshot"])
                    )
                    if reason:
                        stop = reason
                        uow.commit()
                        break
                    plan = MutationPlan.model_validate_json(recipe["plan_json"]).model_copy(
                        update={"strength": level / 1000}
                    )
                    child = s.candidates.create(
                        e["id"],
                        CreateMutation(plan=plan, rng_seed=recipe["rng_seed"]),
                        f"minimize:{job['id']}:{len(cp['probes']) + 1}",
                        refinement_job_id=job["id"],
                    )
                    probe = {
                        "mutation_id": str(child.id),
                        "finding_id": None,
                        "strength": child.strength,
                        "outcome": "REVIEW_REQUIRED",
                        "screening_outcome": None,
                        "confirmation_outcomes": [],
                    }
                    cp["probes"].append(probe)
                    if (
                        child.minimization_job_id != job["id"]
                        or child.parent_mutation_id != m["id"]
                    ):
                        probe["outcome"] = "ALIASED"
                        stop = "NO_NEW_IMAGE"
                        s.db.execute(
                            s.t.background_jobs.update()
                            .where(s.t.background_jobs.c.id == job["id"])
                            .values(checkpoint_json=cp)
                        )
                        uow.commit()
                        break
                    cp["awaiting_review"] = True
                    s.db.execute(
                        s.t.background_jobs.update()
                        .where(s.t.background_jobs.c.id == job["id"])
                        .values(checkpoint_json=cp)
                    )
                    complete(
                        s,
                        job,
                        minimization_result(s, job, cp, "REVIEW_REQUIRED"),
                        review=True,
                        pending_review=True,
                    )
                    uow.commit()
                    return
                child = s.candidates.row(UUID(probe["mutation_id"]))
                report = s.candidates.checked_report(child)
                if report.verdict != "VALID":
                    probe["outcome"] = "INVALID" if report.verdict == "INVALID" else "UNKNOWN"
                    stop = "INVALID_PROBE" if report.verdict == "INVALID" else "REVIEW_REQUIRED"
                    s.db.execute(
                        s.t.background_jobs.update()
                        .where(s.t.background_jobs.c.id == job["id"])
                        .values(checkpoint_json=cp)
                    )
                    uow.commit()
                    break
                require_provider_mode(s, e)
                require_mode(settings, e["config_snapshot"]["target"])
                reason = search.batch_fits(s, e, 4, search.experiment_prices(e["config_snapshot"]))
                if reason:
                    stop = reason
                    uow.commit()
                    break
                mid = child["id"]
                uow.commit()
            await search.target_stage(factory, settings, job, "SCREENING", 1, mid)
            with factory() as uow:
                s = Service(uow, settings)
                search.owned(s, job)
                observed = (
                    s.db.execute(
                        select(s.t.model_observations).where(
                            s.t.model_observations.c.mutation_id == mid,
                            s.t.model_observations.c.phase == "SCREENING",
                        )
                    )
                    .mappings()
                    .one()
                )
                probe["screening_outcome"] = observed["outcome"]
                fid = s.db.scalar(
                    select(s.t.findings.c.id).where(s.t.findings.c.mutation_id == mid)
                )
                probe["finding_id"] = str(fid) if fid else None
                uow.commit()
            if observed["outcome"] == "INCORRECT" and fid:
                for attempt in range(1, 4):
                    await search.target_stage(
                        factory, settings, job, "CONFIRMATION", attempt, mid, fid
                    )
                with factory() as uow:
                    s = Service(uow, settings)
                    search.owned(s, job)
                    outcomes = list(
                        s.db.scalars(
                            select(s.t.model_observations.c.outcome)
                            .where(
                                s.t.model_observations.c.mutation_id == mid,
                                s.t.model_observations.c.phase == "CONFIRMATION",
                            )
                            .order_by(s.t.model_observations.c.attempt_no)
                        )
                    )
                    probe["confirmation_outcomes"] = outcomes
                    status = s.finding(fid)["status"]
                    probe["outcome"] = (
                        "STABLE"
                        if status == "STABLE"
                        else "INTERMITTENT"
                        if status == "INTERMITTENT"
                        else "NOT_REPRODUCED"
                        if status == "NOT_REPRODUCED"
                        else "UNKNOWN"
                    )
                    uow.commit()
            else:
                probe["outcome"] = "PASSING" if observed["outcome"] == "CORRECT" else "UNKNOWN"
            with factory() as uow:
                s = Service(uow, settings)
                search.owned(s, job)
                level = int(Decimal(str(probe["strength"])) * 1000)
                if probe["outcome"] == "STABLE":
                    cp.update(best_mutation_id=str(mid), best_finding_id=str(fid), best_level=level)
                elif probe["outcome"] == "PASSING":
                    cp["boundary_level"] = level
                else:
                    stop = probe["outcome"] + "_PROBE"
                s.db.execute(
                    s.t.background_jobs.update()
                    .where(s.t.background_jobs.c.id == job["id"])
                    .values(checkpoint_json=cp)
                )
                uow.commit()
            if probe["outcome"] not in {"STABLE", "PASSING"} or (
                probe["outcome"] == "PASSING" and recipe["family"] == "background_clutter"
            ):
                break
    except Exception as exc:
        stop = str(exc.code) if isinstance(exc, ServiceError) else "MINIMIZATION_EXECUTION_FAILED"
    with factory() as uow:
        if fenced(uow, job) is None:
            return
        s = Service(uow, settings)
        reconcile(s, job)
        if not cp:
            cp = s.job(job["id"])["checkpoint_json"] or {
                "best_mutation_id": job["request_json"]["source_mutation_id"],
                "best_finding_id": str(job["finding_id"]),
                "probes": [],
            }
        complete(
            s,
            job,
            minimization_result(s, job, cp, stop),
            review=stop
            in {"MINIMIZATION_EXECUTION_FAILED", "REVIEW_REQUIRED", "PROVIDER_MODE_UNAVAILABLE"},
        )
        uow.commit()


def recover_refinement(s: Any, job: Any, dispatched: bool) -> bool:
    """Retain conservative typed checkpoints; a claimed call is never reissued."""
    if job["kind"] == "RETEST":
        if not dispatched:
            return False
        retest_result = comparison(s, job, "WORKER_LEASE_EXPIRED")
        complete(
            s,
            job,
            JobResult(
                outcome="INCOMPLETE",
                synthetic=s.settings.provider_mode == "FIXTURE",
                retest=retest_result,
                diagnostics=["WORKER_LEASE_EXPIRED"],
            ),
            review=True,
        )
        return True
    cp = job["checkpoint_json"]
    if not cp:
        if not dispatched:
            return False
        cp = {
            "best_mutation_id": job["request_json"]["source_mutation_id"],
            "best_finding_id": str(job["finding_id"]),
            "probes": [],
        }
    pending_review = not dispatched and cp.get("awaiting_review", False)
    if not dispatched and not pending_review:
        return False
    result = minimization_result(
        s, job, cp, "REVIEW_REQUIRED" if pending_review else "WORKER_LEASE_EXPIRED"
    )
    complete(s, job, result, review=True, pending_review=pending_review)
    return True
