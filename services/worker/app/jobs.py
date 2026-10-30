"""Fenced Phase 03 setup jobs. Experiment scheduling remains a separate phase."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from counterforge_contracts.requests import JobLimits
from counterforge_contracts.responses import JobResult
from counterforge_contracts.versions import ErrorCode
from counterforge_meta.sam import SamSegmenter
from counterforge_meta.transport import MetaTransport
from counterforge_meta.usage import PriceBook
from counterforge_targets.fixture import FixtureTarget
from counterforge_targets.meta_spark import MetaSparkTarget
from sqlalchemy import func, select

from services.api.app.accounting import Budget
from services.api.app.config import Settings
from services.api.app.errors import ServiceError
from services.api.app.masks import decode_mask
from services.api.app.provider_accounting import Accounting
from services.api.app.service import Service

from .fixtures import mock_transport, require_seed

LEASE = timedelta(seconds=30)


def claim_job(factory: Any, settings: Settings, job_id: UUID) -> dict[str, Any] | None:
    with factory() as uow:
        service = Service(uow, settings)
        job = service.row(uow.tables.background_jobs, job_id, lock=True)
        service.project(job["project_id"])
        if job["state"] != "QUEUED" or job["kind"] not in {
            "SEGMENT",
            "TARGET_CHECK",
            "SCENE",
            "MUTATION_SAM",
            "EXPORT",
            "MINIMIZE",
            "RETEST",
        }:
            return None
        token = uuid4()
        table = uow.tables.background_jobs
        row = (
            uow.connection.execute(
                table.update()
                .where(table.c.id == job_id)
                .values(
                    state="RUNNING",
                    claim_token=token,
                    delivery_no=table.c.delivery_no + 1,
                    started_at=func.coalesce(table.c.started_at, func.now()),
                    heartbeat_at=func.now(),
                    lease_expires_at=func.now() + LEASE,
                    updated_at=func.now(),
                )
                .returning(table)
            )
            .mappings()
            .one()
        )
        result = dict(row)
        uow.commit()
        return result


def fenced(uow: Any, job: dict[str, Any]) -> Any:
    table = uow.tables.background_jobs
    row = (
        uow.connection.execute(select(table).where(table.c.id == job["id"]).with_for_update())
        .mappings()
        .one()
    )
    if (
        row["state"] != "RUNNING"
        or row["claim_token"] != job["claim_token"]
        or row["delivery_no"] != job["delivery_no"]
        or row["lease_expires_at"] is None
        or row["lease_expires_at"] <= uow.connection.scalar(select(func.now()))
    ):
        return None
    return row


async def heartbeat(factory: Any, job: dict[str, Any]) -> None:
    while True:
        await asyncio.sleep(5)
        with factory() as uow:
            if fenced(uow, job) is None:
                return
            table = uow.tables.background_jobs
            uow.connection.execute(
                table.update()
                .where(table.c.id == job["id"])
                .values(heartbeat_at=func.now(), lease_expires_at=func.now() + LEASE)
            )
            uow.commit()


def recover_jobs(factory: Any, settings: Settings) -> list[UUID]:
    """No paid retry after a dispatch claim, including a lost successful output."""
    with factory() as uow:
        table, usage = uow.tables.background_jobs, uow.tables.usage_events
        rows = (
            uow.connection.execute(
                select(table)
                .where(
                    table.c.state == "RUNNING",
                    table.c.project_id.in_(
                        select(uow.tables.projects.c.id).where(
                            uow.tables.projects.c.owner_label == settings.owner_label
                        )
                    ),
                    table.c.kind.in_(
                        [
                            "SEGMENT",
                            "TARGET_CHECK",
                            "SCENE",
                            "MUTATION_SAM",
                            "EXPORT",
                            "MINIMIZE",
                            "RETEST",
                        ]
                    ),
                    table.c.lease_expires_at <= func.now(),
                )
                .with_for_update(skip_locked=True)
            )
            .mappings()
            .all()
        )
        service = Service(uow, settings)
        for job in rows:
            service.project(job["project_id"])
            events = (
                uow.connection.execute(select(usage).where(usage.c.background_job_id == job["id"]))
                .mappings()
                .all()
            )
            dispatched = any(e["dispatched_at"] is not None for e in events)
            budget = Budget(uow)
            for event in events:
                if event["status"] == "RESERVED":
                    if event["dispatched_at"] is None:
                        budget.void_operation(event["operation_key"])
                    else:
                        budget.mark_unknown_charge(event["operation_key"], "WORKER_LEASE_EXPIRED")
            if job["kind"] in {"MINIMIZE", "RETEST"}:
                from .refinement_jobs import recover_refinement

                if recover_refinement(service, job, dispatched):
                    continue
            uow.connection.execute(
                table.update()
                .where(table.c.id == job["id"])
                .values(
                    state="REVIEW_REQUIRED" if dispatched else "QUEUED",
                    claim_token=None,
                    lease_expires_at=None,
                    updated_at=func.now(),
                    finished_at=func.now() if dispatched else None,
                    error_code="REVIEW_REQUIRED" if dispatched else None,
                )
            )
        queued = list(
            uow.connection.scalars(
                select(table.c.id)
                .join(uow.tables.projects, uow.tables.projects.c.id == table.c.project_id)
                .where(
                    table.c.state == "QUEUED",
                    table.c.kind.in_(
                        [
                            "SEGMENT",
                            "TARGET_CHECK",
                            "SCENE",
                            "MUTATION_SAM",
                            "EXPORT",
                            "MINIMIZE",
                            "RETEST",
                        ]
                    ),
                    uow.tables.projects.c.owner_label == settings.owner_label,
                )
            )
        )
        uow.commit()
        return queued


def finish_job(
    factory: Any,
    settings: Settings,
    job: dict[str, Any],
    result: JobResult,
    *,
    seed_id: UUID | None = None,
    proposals: list[Any] | None = None,
    object_label: str = "",
) -> bool:
    with factory() as uow:
        row = fenced(uow, job)
        if row is None:
            return False
        service = Service(uow, settings)
        service.project(row["project_id"])
        applied = True
        if seed_id is not None:
            applied = service.mask_review.apply_proposals(
                seed_id,
                job_id=job["id"],
                expected_generation=job["request_json"]["mask_generation"],
                proposals=proposals or [],
                object_label=object_label,
                outcome=result.outcome,
                diagnostics=result.diagnostics,
                synthetic=result.synthetic,
            )
        if not applied:
            result = result.model_copy(
                update={"diagnostics": [*result.diagnostics, "HUMAN_DRAFT_CHANGED"]}
            )
        success = result.outcome == "COMPLETED" and applied
        table = uow.tables.background_jobs
        uow.connection.execute(
            table.update()
            .where(table.c.id == job["id"])
            .values(
                state="SUCCEEDED" if success else "REVIEW_REQUIRED",
                result_json=result.model_dump(mode="json"),
                error_code=None if success else "REVIEW_REQUIRED",
                finished_at=func.now(),
                updated_at=func.now(),
                lease_expires_at=None,
            )
        )
        uow.commit()
        return True


async def execute_job(factory: Any, settings: Settings, job_id: UUID) -> None:
    job = claim_job(factory, settings, job_id)
    if job is None:
        return
    pulse = asyncio.create_task(heartbeat(factory, job))
    synthetic = settings.provider_mode == "FIXTURE"
    try:
        if job["kind"] in {"MINIMIZE", "RETEST"}:
            from .refinement_jobs import execute_minimize, execute_retest

            await (execute_minimize if job["kind"] == "MINIMIZE" else execute_retest)(
                factory, settings, job
            )
            return
        if job["kind"] == "EXPORT":
            from .export_jobs import execute_export

            await execute_export(factory, settings, job)
            return
        if job["kind"] in {"SCENE", "MUTATION_SAM"}:
            from .candidate_jobs import execute_preparation

            await execute_preparation(factory, settings, job)
            return
        request = job["request_json"]
        if settings.provider_mode == "DISABLED":
            raise ValueError("PROVIDERS_DISABLED")
        if "seed_id" not in request:
            raise ValueError("INVALID_INPUT")
        if request.get("limits") is None:
            raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Explicit job limits are required.")
        limits = JobLimits.model_validate(request["limits"])
        with factory() as clock_uow:
            now = clock_uow.connection.scalar(select(func.clock_timestamp()))
        remaining = limits.max_runtime_seconds - (now - job["started_at"]).total_seconds()
        if remaining <= 0:
            raise ServiceError(ErrorCode.BUDGET_EXCEEDED, "Job runtime limit was reached.")
        # Dispatch checks alone cannot bound parsing and mask decoding. Cancellation
        # leaves transport reconciliation shielded and never applies partial drafts.
        async with asyncio.timeout(remaining):
            with factory() as uow:
                service = Service(uow, settings)
                seed = service.seed(UUID(request["seed_id"]))
                if seed["project_id"] != job["project_id"]:
                    raise ValueError("INVALID_INPUT")
                image = service.artifacts.read_verified(seed["image_asset_id"])
                asset = service.asset(seed["image_asset_id"])
                width, height, expected = (
                    asset["width"],
                    asset["height"],
                    seed["expected_json"]["value"],
                )
                if job["kind"] == "TARGET_CHECK":
                    target = dict(service.target(UUID(request["target_config_id"])))
                    if not target["enabled"] or target["project_id"] != job["project_id"]:
                        raise ValueError("INVALID_INPUT")
                    pinned = request.get("target_snapshot")
                    current = {
                        key: target[key]
                        for key in (
                            "adapter_key",
                            "model_id",
                            "model_revision",
                            "prompt_template",
                            "inference_options",
                        )
                    }
                    if pinned != current:
                        raise ValueError("TARGET_SETTINGS_CHANGED")
            if synthetic:
                require_seed(image)
            accounting = Accounting(
                factory,
                settings,
                job_id=job["id"],
                claim_token=job["claim_token"],
                delivery=job["delivery_no"],
                purpose=job["kind"],
                synthetic=synthetic,
            )
            if job["kind"] == "TARGET_CHECK" and target["adapter_key"] == "LOCAL_FIXTURE":
                if not synthetic:
                    raise ValueError("LOCAL_FIXTURE_REQUIRES_FIXTURE_MODE")
                with factory() as uow:
                    Budget(uow)._job_limits(Service(uow, settings).job(job["id"]))
                    allowance = Budget(uow).deployment_allowance()
                    if allowance is None or allowance.expires_at <= uow.connection.scalar(
                        select(func.now())
                    ):
                        raise ValueError("ALLOWANCE_REQUIRED")
                # Local deterministic evaluation makes no provider call and incurs no billing.
                from hashlib import sha256

                evaluated = await FixtureTarget(
                    {sha256(image).hexdigest(): "5"}, expected_count=expected
                ).evaluate(image, target["prompt_template"], target["inference_options"])
                finish_job(
                    factory,
                    settings,
                    job,
                    JobResult(
                        outcome="COMPLETED",
                        synthetic=True,
                        parsed_count=evaluated.parsed_answer,
                        target_status=evaluated.status,
                        expected_count=expected,
                    ),
                )
                return
            prices = PriceBook.load(settings.pricing_path)
            async with MetaTransport(
                api_key="synthetic-fixture-key"
                if synthetic
                else settings.model_api_key.get_secret_value(),
                accounting=accounting,
                prices=prices,
                http_transport=mock_transport() if synthetic else None,
                deadline_seconds=min(45.0, remaining),
            ) as transport:
                if job["kind"] == "SEGMENT":
                    proposals: list[Any] = []

                    async def writer(object_id: str, data: bytes) -> UUID:
                        mask = await asyncio.to_thread(
                            decode_mask, data, width=width, height=height
                        )
                        with factory() as uow:
                            service = Service(uow, settings)
                            service.project(job["project_id"])
                            artifact = service.artifacts.put(
                                job["project_id"],
                                mask.png,
                                "MASK",
                                "image/png",
                                width=width,
                                height=height,
                                metadata={"source_job_id": str(job["id"]), "synthetic": synthetic},
                            )
                            uow.commit()
                        proposals.append((artifact.id, mask.geometry()))
                        return artifact.id

                    segmented = await SamSegmenter(transport=transport, mask_writer=writer).segment(
                        image, request["concept"]
                    )
                    result = JobResult(
                        outcome=segmented.outcome,
                        synthetic=synthetic,
                        detected_count=len(segmented.objects),
                        expected_count=expected,
                        mask_asset_ids=[obj.mask_asset_id for obj in segmented.objects],
                        diagnostics=list(segmented.diagnostics),
                    )
                    finish_job(
                        factory,
                        settings,
                        job,
                        result,
                        seed_id=seed["id"],
                        proposals=proposals,
                        object_label=request["concept"],
                    )
                else:
                    if (
                        target["adapter_key"] != "META_SPARK"
                        or target["model_id"] != "muse-spark-1.3"
                    ):
                        raise ValueError("INVALID_INPUT")
                    evaluated = await MetaSparkTarget(
                        transport=transport, expected_count=expected
                    ).evaluate(image, target["prompt_template"], target["inference_options"])
                    finish_job(
                        factory,
                        settings,
                        job,
                        JobResult(
                            outcome="COMPLETED"
                            if evaluated.parsed_answer is not None
                            else "INCOMPLETE",
                            synthetic=synthetic,
                            parsed_count=evaluated.parsed_answer,
                            target_status=evaluated.status,
                            expected_count=expected,
                        ),
                    )
    except Exception as exc:
        # Persist no exception text: SDK/database errors can contain secrets or payloads.
        diagnostic = (
            "BUDGET_EXCEEDED"
            if isinstance(exc, TimeoutError)
            else "INVALID_INPUT"
            if isinstance(exc, ValueError)
            else "JOB_EXECUTION_FAILED"
        )
        if isinstance(exc, ServiceError):
            diagnostic = str(exc.code)
        with factory() as uow:
            if fenced(uow, job) is not None:
                usage = uow.tables.usage_events
                for event in (
                    uow.connection.execute(
                        select(usage).where(
                            usage.c.background_job_id == job_id,
                            usage.c.status == "RESERVED",
                            usage.c.operation_key.like(f"{job_id}:{job['delivery_no']}:%"),
                        )
                    )
                    .mappings()
                    .all()
                ):
                    if event["dispatched_at"] is None:
                        Budget(uow).void_operation(event["operation_key"])
                    else:
                        Budget(uow).mark_unknown_charge(
                            event["operation_key"], "JOB_EXECUTION_FAILED"
                        )
                table = uow.tables.background_jobs
                uow.connection.execute(
                    table.update()
                    .where(table.c.id == job_id)
                    .values(
                        state="REVIEW_REQUIRED",
                        error_code=diagnostic,
                        result_json=JobResult(
                            outcome="INCOMPLETE", synthetic=synthetic, diagnostics=[diagnostic]
                        ).model_dump(mode="json"),
                        finished_at=func.now(),
                        lease_expires_at=None,
                    )
                )
                uow.commit()
    finally:
        pulse.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await pulse
