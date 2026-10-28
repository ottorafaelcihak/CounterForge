"""Explicitly authorized four-capability smoke; no implicit limits or allowance."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from decimal import Decimal
from typing import Any
from uuid import UUID

from counterforge_contracts.requests import JobLimits
from counterforge_contracts.responses import JobResult
from counterforge_contracts.vtc import VisualTestContract
from counterforge_meta.image import SceneGenerator
from counterforge_meta.sam import SamSegmenter
from counterforge_meta.spark import SparkPlanner
from counterforge_meta.transport import MetaTransport, inspect_image
from counterforge_meta.usage import PriceBook
from counterforge_targets.meta_spark import MetaSparkTarget
from sqlalchemy import func, select

from services.api.app.accounting import Budget
from services.api.app.config import Settings
from services.api.app.db import create_database_engine, create_uow_factory
from services.api.app.masks import decode_mask
from services.api.app.provider_accounting import Accounting
from services.api.app.service import Service
from services.worker.app.fixtures import mock_transport, require_seed
from services.worker.app.jobs import claim_job, finish_job, heartbeat


class SmokeFailure(Exception):
    """Public failure identity contains only a job UUID and fixed diagnostic code."""

    def __init__(self, job_id: UUID, code: str) -> None:
        self.job_id, self.code = job_id, code
        super().__init__(code)


def smoke_inputs(env: Any) -> tuple[JobLimits, UUID, UUID, UUID]:
    if env.get("RUN_META_LIVE_TESTS") != "1" or env.get("PROVIDER_MODE") != "LIVE":
        raise ValueError("Explicit LIVE smoke gate is required")
    usd = Decimal(env["SMOKE_META_MAX_USD"])
    if not usd.is_finite() or not 0 < usd <= 150:
        raise ValueError("Finite dollar limit is required")
    limits = JobLimits(
        max_estimated_usd=float(usd),
        max_calls=int(env["SMOKE_META_MAX_CALLS"]),
        max_runtime_seconds=int(env["SMOKE_META_MAX_RUNTIME_SECONDS"]),
    )
    if limits.max_calls < 4:
        raise ValueError("Four capability calls must be explicitly authorized")
    return (
        limits,
        UUID(env["SMOKE_META_SEED_ID"]),
        UUID(env["SMOKE_META_VTC_ID"]),
        UUID(env["SMOKE_META_TARGET_ID"]),
    )


async def smoke(
    factory: Any,
    settings: Settings,
    limits: JobLimits,
    seed_id: UUID,
    contract_id: UUID,
    target_id: UUID,
) -> dict[str, Any]:
    synthetic = settings.provider_mode == "FIXTURE"
    if synthetic:
        if not settings.allow_synthetic_fixtures or settings.app_env not in {"development", "test"}:
            raise ValueError("Fixture gate is closed")
    elif settings.provider_mode != "LIVE" or not settings.run_meta_live_tests:
        raise ValueError("Live gate is closed")
    prices = PriceBook.load(settings.pricing_path)
    with factory() as uow:
        service = Service(uow, settings)
        seed_row = service.seed(seed_id)
        seed = dict(seed_row)
        contract_row = service.contract(contract_id)
        target = dict(service.target(target_id))
        if (
            contract_row["seed_id"] != seed_id
            or target["project_id"] != seed["project_id"]
            or not target["enabled"]
            or target["adapter_key"] != "META_SPARK"
            or target["model_id"] != "muse-spark-1.3"
        ):
            raise ValueError("Existing owned seed, VTC and enabled Meta target must agree")
        contract = VisualTestContract.model_validate(contract_row["contract_json"])
        service.contract_evidence(seed_row, contract)
        source = service.artifacts.read_verified(seed["image_asset_id"])
        if synthetic:
            require_seed(source)
        asset = service.asset(seed["image_asset_id"])
        width, height = asset["width"], asset["height"]
        allowance = Budget(uow).deployment_allowance()
        now = uow.connection.scalar(select(func.now()))
        if (
            allowance is None
            or allowance.expires_at <= now
            or allowance.max_calls - allowance.consumed_calls < 4
        ):
            raise ValueError("Existing operator deployment allowance is required")
        upper = (
            prices.upper("muse-spark-1.3", 1024)
            + prices.upper("sam-3.1")
            + prices.upper("muse-image-1.0")
            + prices.upper("muse-spark-1.3", target["inference_options"]["max_output_tokens"])
        )
        if synthetic:
            upper = Decimal(0)
        if (
            upper > Decimal(str(limits.max_estimated_usd))
            or upper > allowance.max_usd - allowance.consumed_usd
        ):
            raise ValueError("Existing dollar allowances cannot cover conservative upper bounds")
        job_id = uow.background_jobs.enqueue(
            project_id=seed["project_id"],
            kind="SEGMENT",
            request_json={
                "seed_id": str(seed_id),
                "contract_id": str(contract_id),
                "target_config_id": str(target_id),
                "smoke": True,
                "limits": limits.model_dump(mode="json"),
            },
        )
        uow.commit()
    job = claim_job(factory, settings, job_id)
    if job is None:
        raise ValueError("Smoke claim was not acquired")
    pulse = asyncio.create_task(heartbeat(factory, job))
    mask_ids: list[UUID] = []
    evidence: list[UUID] = []
    try:
        async with asyncio.timeout(limits.max_runtime_seconds):

            def transport(purpose: str) -> MetaTransport:
                return MetaTransport(
                    api_key="synthetic-fixture-key"
                    if synthetic
                    else settings.model_api_key.get_secret_value(),
                    http_transport=mock_transport() if synthetic else None,
                    prices=prices,
                    accounting=Accounting(
                        factory,
                        settings,
                        job_id=job_id,
                        claim_token=job["claim_token"],
                        delivery=job["delivery_no"],
                        purpose=purpose,
                        synthetic=synthetic,
                    ),
                )

            async with transport("SMOKE_PLANNER") as wire:
                planner = SparkPlanner(transport=wire)
                plans = await planner.plan(contract, [], contract.limits)
                with factory() as uow:
                    stored = Service(uow, settings).artifacts.put(
                        seed["project_id"],
                        json.dumps(
                            {
                                "planner": planner.last_evidence,
                                "plans": [p.model_dump(mode="json") for p in plans],
                            },
                            sort_keys=True,
                        ).encode(),
                        "OTHER",
                        "application/json",
                    )
                    evidence.append(stored.id)
                    uow.commit()

            async def writer(object_id: str, data: bytes) -> UUID:
                mask = await asyncio.to_thread(decode_mask, data, width=width, height=height)
                with factory() as uow:
                    stored = Service(uow, settings).artifacts.put(
                        seed["project_id"],
                        mask.png,
                        "MASK",
                        "image/png",
                        width=width,
                        height=height,
                    )
                    uow.commit()
                mask_ids.append(stored.id)
                return stored.id

            async with transport("SMOKE_SAM") as wire:
                segmented = await SamSegmenter(transport=wire, mask_writer=writer).segment(
                    source, contract.protected.label
                )
            async with transport("SMOKE_IMAGE") as wire:
                image = await SceneGenerator(transport=wire).create_scene(
                    source,
                    "Change only background brightness subtly. Preserve all protected objects exactly.",
                )
                from services.api.app.artifacts import normalize_image

                media, _, _ = inspect_image(image)
                normalized = normalize_image(image, media)
                with factory() as uow:
                    stored = Service(uow, settings).artifacts.put(
                        seed["project_id"],
                        normalized.png,
                        "OTHER",
                        "image/png",
                        width=normalized.width,
                        height=normalized.height,
                    )
                    evidence.append(stored.id)
                    uow.commit()
            async with transport("SMOKE_TARGET") as wire:
                evaluated = await MetaSparkTarget(
                    transport=wire, expected_count=seed["expected_json"]["value"]
                ).evaluate(source, target["prompt_template"], target["inference_options"])
            with factory() as uow:
                usage = uow.tables.usage_events
                evidence.extend(
                    uow.connection.scalars(
                        select(usage.c.raw_response_asset_id).where(
                            usage.c.background_job_id == job_id,
                            usage.c.raw_response_asset_id.is_not(None),
                        )
                    )
                )
            complete = (
                segmented.outcome == "COMPLETED"
                and evaluated.parsed_answer is not None
                and planner.last_evidence.get("author") == "META_SPARK"
            )
            result = JobResult(
                outcome="COMPLETED" if complete else "INCOMPLETE",
                synthetic=synthetic,
                detected_count=len(segmented.objects),
                expected_count=seed["expected_json"]["value"],
                mask_asset_ids=mask_ids,
                parsed_count=evaluated.parsed_answer,
                target_status=evaluated.status,
                diagnostics=list(segmented.diagnostics),
                evidence_asset_ids=evidence,
            )
            finished = finish_job(factory, settings, job, result)
            if not finished:
                raise ValueError("SMOKE_COMPLETION_FENCE_LOST")
            with factory() as uow:
                complete = (
                    complete
                    and finished
                    and Service(uow, settings).job(job_id)["state"] == "SUCCEEDED"
                )
            return {
                "job_id": str(job_id),
                "live_gate": "VERIFIED_CAPABILITIES" if complete and not synthetic else "UNMET",
                "synthetic": synthetic,
                "capabilities_complete": complete,
                "evidence_asset_ids": [str(value) for value in evidence],
            }
    except BaseException as exc:
        with factory() as uow:
            table, usage = uow.tables.background_jobs, uow.tables.usage_events
            row = (
                uow.connection.execute(select(table).where(table.c.id == job_id).with_for_update())
                .mappings()
                .one()
            )
            if (
                row["claim_token"] == job["claim_token"]
                and row["delivery_no"] == job["delivery_no"]
                and row["state"] == "RUNNING"
            ):
                for event in uow.connection.execute(
                    select(usage).where(
                        usage.c.background_job_id == job_id,
                        usage.c.status == "RESERVED",
                        usage.c.operation_key.like(f"{job_id}:{job['delivery_no']}:%"),
                    )
                ).mappings():
                    if event["dispatched_at"] is None:
                        Budget(uow).void_operation(event["operation_key"])
                    else:
                        Budget(uow).mark_unknown_charge(event["operation_key"], "SMOKE_INTERRUPTED")
                uow.connection.execute(
                    table.update()
                    .where(table.c.id == job_id)
                    .values(
                        state="REVIEW_REQUIRED",
                        error_code="REVIEW_REQUIRED",
                        result_json=JobResult(
                            outcome="INCOMPLETE",
                            synthetic=synthetic,
                            diagnostics=["SMOKE_INTERRUPTED"],
                            evidence_asset_ids=evidence,
                        ).model_dump(mode="json"),
                        finished_at=func.now(),
                        lease_expires_at=None,
                    )
                )
                uow.commit()
        if isinstance(exc, Exception):
            from services.api.app.errors import ServiceError

            code = (
                str(exc.code)
                if isinstance(exc, ServiceError)
                else "BUDGET_EXCEEDED"
                if isinstance(exc, TimeoutError)
                else "SMOKE_EXECUTION_FAILED"
            )
            raise SmokeFailure(job_id, code) from None
        raise
    finally:
        pulse.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await pulse


def main() -> None:
    engine = None
    try:
        limits, seed, contract, target = smoke_inputs(os.environ)
        settings = Settings.from_env()
        engine = create_database_engine(settings)
        result = asyncio.run(
            smoke(create_uow_factory(engine), settings, limits, seed, contract, target)
        )
        print(json.dumps(result))
        if result["live_gate"] == "UNMET":
            raise SystemExit(1)
    except SmokeFailure as failure:
        print(
            json.dumps(
                {"live_gate": "UNMET", "job_id": str(failure.job_id), "error_code": failure.code}
            )
        )
        raise SystemExit(2) from None
    except Exception:
        print(
            "Live gate UNMET: check explicit limits, owned references, provider authorization and existing deployment allowance."
        )
        raise SystemExit(2) from None
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
