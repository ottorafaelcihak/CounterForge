from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from counterforge_contracts.requests import SegmentationRequest
from counterforge_meta.usage import CallIntent, Charge, PriceBook
from sqlalchemy import func, select

from services.api.app.accounting import Budget
from services.api.app.provider_accounting import Accounting
from services.api.app.service import Service
from services.worker.app.fixtures import seed_bytes
from services.worker.app.jobs import claim_job, execute_job, recover_jobs


def fixture_settings(api):
    return api.settings.model_copy(
        update={"provider_mode": "FIXTURE", "allow_synthetic_fixtures": True}
    )


def queued(api, *, synthetic=True):
    settings = (
        fixture_settings(api)
        if synthetic
        else api.settings.model_copy(update={"provider_mode": "LIVE"})
    )
    with api.factory() as uow:
        s = Service(uow, settings)
        seed = s.create_seed(
            UUID(api.project_id),
            seed_bytes(),
            "image/png",
            question="How many blue containers?",
            expected_count=5,
            license_ack=True,
            label_reviewer="owner",
        )
        job = s.segment(
            seed.id,
            SegmentationRequest(
                concept="blue container",
                limits={"max_estimated_usd": 5, "max_calls": 3, "max_runtime_seconds": 120},
            ),
            "fixture-segment",
        )
        Budget(uow).configure_deployment_allowance(
            max_usd=Decimal(0) if synthetic else Decimal(5),
            max_calls=10,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        uow.commit()
    return settings, seed.id, job.job_id


async def test_sdk_fixture_segment_four_masks_then_target_five_and_duplicate_noop(api):
    settings, seed_id, job_id = queued(api)
    await execute_job(api.factory, settings, job_id)
    await execute_job(api.factory, settings, job_id)
    with api.factory() as uow:
        s = Service(uow, settings)
        job = s.job_dto(s.job(job_id))
        assert job.state == "SUCCEEDED", job
        assert (
            job.result.synthetic
            and job.result.detected_count == 4
            and job.result.expected_count == 5
        )
        assert len(job.result.mask_asset_ids) == 4
        events = uow.connection.execute(select(uow.tables.usage_events)).mappings().all()
        assert (
            len(events) == 1 and events[0]["actual_usd"] == 0 and events[0]["raw_response_asset_id"]
        )
        masks = s.mask_review.state(seed_id)
        assert masks.detected_count == 4 and not masks.can_create_contract
        from counterforge_contracts.requests import CreateTargetConfig, TargetCheckRequest

        target = s.create_target(
            UUID(api.project_id),
            CreateTargetConfig(
                adapter_key="META_SPARK",
                model_id="muse-spark-1.3",
                prompt_template="How many blue containers?",
            ),
        )
        checked = s.check_target(
            target.id,
            "fixture-target",
            TargetCheckRequest(
                seed_id=seed_id,
                limits={"max_estimated_usd": 5, "max_calls": 3, "max_runtime_seconds": 120},
            ),
        )
        uow.commit()
    await execute_job(api.factory, settings, checked.job_id)
    with api.factory() as uow:
        s = Service(uow, settings)
        target_job = s.job_dto(s.job(checked.job_id))
        assert target_job.state == "SUCCEEDED", target_job
        assert target_job.result.parsed_count == 5 and target_job.result.synthetic


@pytest.mark.parametrize("kind", ["ACTUAL", "ESTIMATED", "UNKNOWN", "UNBILLED"])
async def test_accounting_persists_charge_kinds_evidence_and_exact_finish_replay(api, kind):
    settings, _, job_id = queued(api, synthetic=False)
    job = claim_job(api.factory, settings, job_id)
    a = Accounting(
        api.factory,
        settings,
        job_id=job_id,
        claim_token=job["claim_token"],
        delivery=job["delivery_no"],
        purpose="SAM",
    )
    intent = CallIntent("sam-3.1", "SAM", "a" * 64, "test-v1", Decimal("0.1"), 1)
    ticket = await a.reserve(intent)
    assert await ticket.claim()
    charge = Charge(
        kind,
        Decimal("0") if kind == "UNBILLED" else Decimal("0.1"),
        kind != "UNBILLED",
        "CONNECT_FAILED" if kind == "UNBILLED" else "MISSING_USAGE",
    )
    result = await ticket.finish(
        "COMPLETED",
        charge,
        {"latency_ms": 1, "usage": {}},
        {"status": "completed", "headers": "secret"},
    )
    assert (
        await ticket.finish(
            "COMPLETED",
            charge,
            {"latency_ms": 1, "usage": {}},
            {"status": "completed", "headers": "secret"},
        )
        == result
    )
    from services.api.app.errors import ServiceError

    with pytest.raises(ServiceError):
        await ticket.finish("REFUSED", charge, {"latency_ms": 1}, {})
    with api.factory() as uow:
        row = uow.connection.execute(select(uow.tables.usage_events)).mappings().one()
        assert row["cost_kind"] == kind and row["raw_response_asset_id"]
        assert (
            row["actual_usd"] is None
            if kind in {"ESTIMATED", "UNKNOWN"}
            else row["actual_usd"] is not None
        )
        if kind == "UNBILLED":
            assert row["metadata_json"]["nonactual_proof"]["proof_code"] == "CONNECT_FAILED"


@pytest.mark.parametrize("dispatched", [False, True])
async def test_recovery_voids_unclaimed_or_quarantines_dispatched_without_retry(api, dispatched):
    settings, _, job_id = queued(api, synthetic=False)
    job = claim_job(api.factory, settings, job_id)
    a = Accounting(
        api.factory,
        settings,
        job_id=job_id,
        claim_token=job["claim_token"],
        delivery=job["delivery_no"],
        purpose="SAM",
    )
    ticket = await a.reserve(CallIntent("sam-3.1", "SAM", "a" * 64, "test", Decimal("0.1"), 1))
    if dispatched:
        assert await ticket.claim()
        ticket.release()
    with api.factory() as uow:
        t = uow.tables.background_jobs
        uow.connection.execute(
            t.update()
            .where(t.c.id == job_id)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        uow.commit()
    recovered = recover_jobs(api.factory, settings)
    assert (job_id in recovered) != dispatched
    with api.factory() as uow:
        row = uow.connection.execute(select(uow.tables.usage_events)).mappings().one()
        assert row["status"] == ("UNKNOWN_CHARGE" if dispatched else "VOID")
        new = claim_job(api.factory, settings, job_id)
        assert (new is None) == dispatched
        if new:
            assert new["delivery_no"] == 2


async def test_missing_target_seed_requires_review_with_zero_calls(api):
    from counterforge_contracts.requests import CreateTargetConfig

    settings = fixture_settings(api)
    with api.factory() as uow:
        s = Service(uow, settings)
        target = s.create_target(
            UUID(api.project_id),
            CreateTargetConfig(
                adapter_key="LOCAL_FIXTURE",
                model_id="LOCAL_FIXTURE",
                prompt_template="Count blue containers",
            ),
        )
        job = s.check_target(target.id, None)
        uow.commit()
    await execute_job(api.factory, settings, job.job_id)
    with api.factory() as uow:
        s = Service(uow, settings)
        result = s.job_dto(s.job(job.job_id))
        assert result.state == "REVIEW_REQUIRED" and result.error_code == "INVALID_INPUT"
        assert uow.connection.scalar(select(func.count()).select_from(uow.tables.usage_events)) == 0


async def test_session_lease_serializes_jobs_and_busy_claim_refunds_reservation(api):
    settings, _, first_id = queued(api, synthetic=False)
    with api.factory() as uow:
        second_id = uow.background_jobs.enqueue(
            project_id=UUID(api.project_id),
            kind="SEGMENT",
            request_json={
                "limits": {"max_estimated_usd": 5, "max_calls": 3, "max_runtime_seconds": 120}
            },
        )
        uow.commit()
    first, second = [claim_job(api.factory, settings, value) for value in (first_id, second_id)]

    def accounting(job):
        return Accounting(
            api.factory,
            settings,
            job_id=job["id"],
            claim_token=job["claim_token"],
            delivery=job["delivery_no"],
            purpose="SAM",
        )

    intent = CallIntent("sam-3.1", "SAM", "a" * 64, "test", Decimal("0.1"), 1)
    one, two = await accounting(first).reserve(intent), await accounting(second).reserve(intent)
    assert await one.claim()
    try:
        assert not one.lease.in_transaction()
        assert not await two.claim()
        with api.factory() as uow:
            usage = (
                uow.connection.execute(
                    select(uow.tables.usage_events).where(
                        uow.tables.usage_events.c.background_job_id == second_id
                    )
                )
                .mappings()
                .one()
            )
            assert usage["status"] == "VOID" and usage["dispatched_at"] is None
            assert Budget(uow).deployment_allowance().consumed_calls == 1
    finally:
        await one.finish(
            "COMPLETED",
            Charge("ACTUAL", Decimal("0.01"), True, "COMPLETED_IMAGES"),
            {"latency_ms": 0},
            {},
        )


async def test_late_segmentation_persists_proposals_without_overwriting_human_draft(api):
    from io import BytesIO

    from PIL import Image

    settings, seed_id, job_id = queued(api)
    image = Image.new("L", (480, 240))
    image.paste(255, (392, 60, 448, 176))
    output = BytesIO()
    image.save(output, "PNG")
    with api.factory() as uow:
        service = Service(uow, settings)
        human = service.mask_review.write(
            seed_id,
            output.getvalue(),
            notes="Human corrected instance",
            object_label="blue container",
        )
        uow.commit()
    await execute_job(api.factory, settings, job_id)
    with api.factory() as uow:
        s = Service(uow, settings)
        result = s.job_dto(s.job(job_id))
        assert result.state == "REVIEW_REQUIRED" and len(result.result.mask_asset_ids) == 4
        assert "HUMAN_DRAFT_CHANGED" in result.result.diagnostics
        current = s.mask_review.current(seed_id)
        assert len(current) == 1 and current[0]["mask_asset_id"] == human.mask_asset_id
        assert s.seed(seed_id)["expected_json"]["value"] == 5


async def test_shrink_expand_segmentation_preserves_distinct_revision_history(api):
    settings, seed_id, job_id = queued(api)
    await execute_job(api.factory, settings, job_id)
    with api.factory() as uow:
        s = Service(uow, settings)
        masks = s.mask_review.current(seed_id)
        proposals = [(m["mask_asset_id"], m["geometry_json"]) for m in masks]
        for count in (2, 4, 1, 4):
            assert s.mask_review.apply_proposals(
                seed_id,
                job_id=job_id,
                expected_generation=s.seed(seed_id)["mask_generation"],
                proposals=proposals[:count],
                object_label="blue container",
                outcome="COMPLETED",
                diagnostics=[],
                synthetic=True,
            )
        assert len(s.mask_review.current(seed_id)) == 4
        assert not any(m["reviewed"] for m in s.mask_review.current(seed_id))
        uow.commit()


async def test_recovery_skips_other_owner_expired_jobs(api):
    settings, _, own = queued(api)
    foreign_settings = settings.model_copy(update={"owner_label": "another-owner"})
    from counterforge_contracts.requests import CreateProject

    with api.factory() as uow:
        s = Service(uow, foreign_settings)
        project = s.create_project(CreateProject(name="Foreign owner"))
        foreign = uow.background_jobs.enqueue(
            project_id=project.id, kind="SEGMENT", request_json={}
        )
        uow.commit()
    claim_job(api.factory, settings, own)
    claim_job(api.factory, foreign_settings, foreign)
    with api.factory() as uow:
        t = uow.tables.background_jobs
        uow.connection.execute(
            t.update().values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        uow.commit()
    assert recover_jobs(api.factory, settings) == [own]
    with api.factory() as uow:
        assert Service(uow, foreign_settings).job(foreign)["state"] == "RUNNING"


async def test_safe_connect_retry_creates_new_durable_reservation(api):
    import httpx
    from counterforge_meta.transport import MetaTransport

    settings, _, job_id = queued(api, synthetic=False)
    job = claim_job(api.factory, settings, job_id)
    accounting = Accounting(
        api.factory,
        settings,
        job_id=job_id,
        claim_token=job["claim_token"],
        delivery=job["delivery_no"],
        purpose="TARGET",
    )
    attempts = []

    def handler(request):
        attempts.append(request.url.path)
        if len(attempts) == 1:
            raise httpx.ConnectError("synthetic connection failure", request=request)
        return httpx.Response(
            200,
            json={
                "id": "retry-fixture",
                "object": "chat.completion",
                "created": 0,
                "model": "muse-spark-1.3",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "5"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    async with MetaTransport(
        api_key="offline-test-key",
        accounting=accounting,
        prices=PriceBook.load(settings.pricing_path),
        http_transport=httpx.MockTransport(handler),
    ) as transport:
        result = await transport.chat(
            messages=[{"role": "user", "content": "Count."}],
            category="TARGET",
            template_version="test-v1",
        )
    assert result.outcome == "COMPLETED" and len(attempts) == 2
    with api.factory() as uow:
        rows = (
            uow.connection.execute(
                select(uow.tables.usage_events).order_by(uow.tables.usage_events.c.attempt_no)
            )
            .mappings()
            .all()
        )
        assert len(rows) == 2 and [r["cost_kind"] for r in rows] == ["UNBILLED", "ACTUAL"]
        assert rows[0]["operation_key"] != rows[1]["operation_key"]
        assert rows[0]["metadata_json"]["nonactual_proof"]["request_sent"] is False


async def test_queue_capacity_deduplicates_before_limit(api):
    from services.api.app.errors import ServiceError

    with api.factory() as uow:
        repository = uow.background_jobs
        repository.max_pending_jobs = 1
        first = repository.enqueue(
            project_id=UUID(api.project_id),
            kind="SEGMENT",
            request_json={},
            idempotency_key="capacity",
        )
        assert (
            repository.enqueue(
                project_id=UUID(api.project_id),
                kind="SEGMENT",
                request_json={},
                idempotency_key="capacity",
            )
            == first
        )
        assert repository.new_job_ids == [first]
        with pytest.raises(ServiceError) as caught:
            repository.enqueue(
                project_id=UUID(api.project_id),
                kind="SEGMENT",
                request_json={},
                idempotency_key="overflow",
            )
        assert caught.value.status == 429
        uow.commit()


async def test_setup_route_is_safe_and_fixture_download_is_explicitly_gated(api):
    response = await api.client.get("/api/v1/setup")
    assert response.status_code == 200 and response.json()["provider_mode"] == "DISABLED"
    assert not response.json()["synthetic_fixture_enabled"]
    assert (await api.client.get("/api/v1/fixtures/development-seed")).status_code == 404
    api.settings.provider_mode = "FIXTURE"
    api.settings.allow_synthetic_fixtures = True
    response = await api.client.get("/api/v1/fixtures/development-seed")
    assert response.status_code == 200 and response.content == seed_bytes()
    assert "key" not in (await api.client.get("/api/v1/setup")).text.lower()


@pytest.mark.parametrize("condition", ["normal", "heartbeat_failure", "lost_completion_fence"])
async def test_four_capability_sdk_fixture_smoke_keeps_live_gate_unmet(
    api, vtc_payload, monkeypatch, condition
):
    from io import BytesIO

    from counterforge_contracts.requests import CreateTargetConfig, JobLimits, ReviewDecision
    from counterforge_contracts.vtc import VisualTestContract
    from PIL import Image

    from scripts import smoke_meta
    from scripts.smoke_meta import smoke

    if condition == "heartbeat_failure":

        async def failed(*args):
            raise RuntimeError("sensitive driver fixture failure")

        monkeypatch.setattr(smoke_meta, "heartbeat", failed)
    elif condition == "lost_completion_fence":
        monkeypatch.setattr(smoke_meta, "finish_job", lambda *args, **kwargs: False)
    from services.worker.app.fixtures import RECTANGLES

    settings, seed_id, _ = queued(api)
    with api.factory() as uow:
        s = Service(uow, settings)
        seed = s.seed(seed_id)
        masks = []
        for rectangle in RECTANGLES:
            image = Image.new("L", (480, 240))
            image.paste(255, rectangle)
            body = BytesIO()
            image.save(body, "PNG")
            mask = s.mask_review.write(
                seed_id,
                body.getvalue(),
                notes="Owner reviewed original rectangle",
                object_label="blue container",
            )
            s.approve_mask(
                seed_id,
                mask.id,
                ReviewDecision(
                    decision="VALID",
                    reason_code="HUMAN_REVIEW",
                    notes="Reviewed exact fixture instance",
                ),
            )
            masks.append(str(mask.mask_asset_id))
        payload = {
            **vtc_payload,
            "case_id": seed["case_key"],
            "prompt": seed["question"],
            "expected": seed["expected_json"],
            "protected": {**vtc_payload["protected"], "mask_asset_ids": masks},
        }
        contract = s.create_contract(seed_id, VisualTestContract.model_validate(payload))
        target = s.create_target(
            UUID(api.project_id),
            CreateTargetConfig(
                adapter_key="META_SPARK",
                model_id="muse-spark-1.3",
                prompt_template="How many blue containers?",
            ),
        )
        uow.commit()
    pending = smoke(
        api.factory,
        settings,
        JobLimits(max_estimated_usd=5.0, max_calls=4, max_runtime_seconds=120),
        seed_id,
        contract.id,
        target.id,
    )
    if condition == "lost_completion_fence":
        with pytest.raises(smoke_meta.SmokeFailure) as error:
            await pending
        result = {"job_id": str(error.value.job_id)}
        assert error.value.code == "SMOKE_EXECUTION_FAILED"
    else:
        result = await pending
        assert (
            result["live_gate"] == "UNMET"
            and result["synthetic"]
            and result["capabilities_complete"]
        )
    with api.factory() as uow:
        s = Service(uow, settings)
        job = s.job_dto(s.job(UUID(result["job_id"])))
        if condition == "lost_completion_fence":
            assert job.state == "REVIEW_REQUIRED" and job.result.outcome == "INCOMPLETE"
        else:
            assert (
                job.state == "SUCCEEDED" and job.result.synthetic and job.result.parsed_count == 5
            )
            assert job.result.detected_count == 4
        assert len(job.result.evidence_asset_ids) == 6
        usage = (
            uow.connection.execute(
                select(uow.tables.usage_events).order_by(uow.tables.usage_events.c.created_at)
            )
            .mappings()
            .all()
        )
        assert [r["category"] for r in usage] == ["PLANNER", "SAM", "IMAGE", "TARGET"]
        assert all(r["actual_usd"] == 0 and r["provider"] == "FIXTURE" for r in usage)
        assert (
            len(s.mask_review.current(seed_id)) == 5
            and s.mask_review.state(seed_id).can_create_contract
        )


async def test_stale_completion_cannot_overwrite_new_delivery(api):
    from counterforge_contracts.responses import JobResult

    from services.worker.app.jobs import finish_job

    settings, _, job_id = queued(api)
    old = claim_job(api.factory, settings, job_id)
    with api.factory() as uow:
        t = uow.tables.background_jobs
        uow.connection.execute(
            t.update()
            .where(t.c.id == job_id)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        uow.commit()
    assert recover_jobs(api.factory, settings) == [job_id]
    new = claim_job(api.factory, settings, job_id)
    assert old["claim_token"] != new["claim_token"] and new["delivery_no"] == 2
    assert not finish_job(
        api.factory, settings, old, JobResult(outcome="COMPLETED", synthetic=True)
    )
    assert finish_job(api.factory, settings, new, JobResult(outcome="COMPLETED", synthetic=True))


async def test_recovery_of_settled_but_lost_output_requires_manual_new_job(api):
    settings, _, job_id = queued(api, synthetic=False)
    job = claim_job(api.factory, settings, job_id)
    accounting = Accounting(
        api.factory,
        settings,
        job_id=job_id,
        claim_token=job["claim_token"],
        delivery=job["delivery_no"],
        purpose="SAM",
    )
    ticket = await accounting.reserve(
        CallIntent("sam-3.1", "SAM", "c" * 64, "test", Decimal("0.1"), 1)
    )
    assert await ticket.claim()
    await ticket.finish(
        "COMPLETED", Charge("ACTUAL", Decimal("0.01"), True, "COMPLETED_IMAGES"), {}, {}
    )
    with api.factory() as uow:
        t = uow.tables.background_jobs
        uow.connection.execute(
            t.update()
            .where(t.c.id == job_id)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        uow.commit()
    assert job_id not in recover_jobs(api.factory, settings)
    with api.factory() as uow:
        assert Service(uow, settings).job(job_id)["state"] == "REVIEW_REQUIRED"
        assert (
            uow.connection.execute(select(uow.tables.usage_events.c.cost_kind)).scalar_one()
            == "ACTUAL"
        )
    assert claim_job(api.factory, settings, job_id) is None


async def test_worker_mask_validation_yields_event_loop_for_heartbeats(api, monkeypatch):
    import threading

    from services.worker.app import jobs

    original = jobs.decode_mask
    validating_threads = []
    main_thread = threading.get_ident()

    def decode(*args, **kwargs):
        validating_threads.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(jobs, "decode_mask", decode)
    settings, _, job_id = queued(api)
    await execute_job(api.factory, settings, job_id)
    assert len(validating_threads) == 4 and all(
        value != main_thread for value in validating_threads
    )


async def test_postcommit_redis_failure_leaves_durable_queued_intent(api, monkeypatch):
    from services.worker.app import queue

    settings, seed_id, _ = queued(api)
    api.settings.enqueue_jobs = True
    committed = []

    def unavailable(*args, **kwargs):
        with api.factory() as uow:
            t = uow.tables.background_jobs
            committed.extend(
                uow.connection.scalars(
                    select(t.c.id).where(t.c.idempotency_key == "redis-unavailable")
                )
            )
        raise RuntimeError("Offline Redis failure")

    monkeypatch.setattr(queue.Redis, "from_url", unavailable)
    result = await api.client.post(
        f"/api/v1/seeds/{seed_id}/segment",
        json={"concept": "blue container"},
        headers={"Idempotency-Key": "redis-unavailable"},
    )
    assert result.status_code == 202 and UUID(result.json()["job_id"]) in committed
    assert result.json()["state"] == "QUEUED"


@pytest.mark.parametrize(
    "sent,proof",
    [(False, "CONNECT_FAILED"), (False, "DEADLINE_BEFORE_SEND"), (True, "UNBILLED_RATE_LIMIT")],
)
async def test_trusted_unbilled_refund_is_idempotent_and_cannot_create_allowance(api, sent, proof):
    settings, _, job_id = queued(api, synthetic=False)
    claim_job(api.factory, settings, job_id)
    with api.factory() as uow:
        budget = Budget(uow)
        budget.reserve_job_operation(job_id, "refund-proof", "SAM", Decimal("0.1"))
        budget.begin_operation("refund-proof")
        budget.finish_unbilled("refund-proof", proof_code=proof, request_sent=sent)
        budget.finish_unbilled("refund-proof", proof_code=proof, request_sent=sent)
        allowance = budget.deployment_allowance()
        assert allowance.consumed_usd == 0 and allowance.consumed_calls == int(sent)
        assert budget._job_usage(job_id) == (Decimal(0), int(sent))
        budget.reserve_job_operation(job_id, "refund-new-reservation", "SAM", Decimal("0.1"))
        assert budget.deployment_allowance().consumed_usd == Decimal("0.1")
        assert budget.deployment_allowance().consumed_calls == int(sent) + 1
        uow.commit()


@pytest.mark.parametrize(
    "sent,proof",
    [(True, "CONNECT_FAILED"), (True, "DEADLINE_BEFORE_SEND"), (False, "UNBILLED_RATE_LIMIT")],
)
async def test_invalid_unbilled_proof_pair_cannot_refund_budget(api, sent, proof):
    from services.api.app.errors import ServiceError

    settings, _, job_id = queued(api, synthetic=False)
    claim_job(api.factory, settings, job_id)
    with api.factory() as uow:
        budget = Budget(uow)
        budget.reserve_job_operation(job_id, "invalid-proof", "SAM", Decimal("0.1"))
        budget.begin_operation("invalid-proof")
        with pytest.raises(ServiceError):
            budget.finish_unbilled("invalid-proof", proof_code=proof, request_sent=sent)
        assert budget.deployment_allowance().consumed_usd == Decimal("0.1")
        assert budget.deployment_allowance().consumed_calls == 1


async def test_job_runtime_cancels_processing_without_overwriting_drafts_or_retrying(
    api, monkeypatch
):
    import asyncio

    from counterforge_contracts.providers import SegmentationResult

    from services.worker.app import jobs

    settings, seed_id, job_id = queued(api)
    original_claim = jobs.claim_job
    cancelled = asyncio.Event()

    def almost_expired(*args):
        job = original_claim(*args)
        with api.factory() as uow:
            table = uow.tables.background_jobs
            started = uow.connection.scalar(
                table.update()
                .where(table.c.id == job_id)
                .values(started_at=func.clock_timestamp() - timedelta(seconds=119.8))
                .returning(table.c.started_at)
            )
            uow.commit()
        job["started_at"] = started
        return job

    async def slow_segmentation(*args, **kwargs):
        try:
            await asyncio.sleep(0.6)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return SegmentationResult(objects=[], outcome="INCOMPLETE", diagnostics=("DELAYED",))

    monkeypatch.setattr(jobs, "claim_job", almost_expired)
    monkeypatch.setattr(jobs.SamSegmenter, "segment", slow_segmentation)
    await jobs.execute_job(api.factory, settings, job_id)
    assert cancelled.is_set(), "The declared runtime must also bound post-dispatch processing"
    with api.factory() as uow:
        service = Service(uow, settings)
        result = service.job_dto(service.job(job_id))
        assert result.state == "REVIEW_REQUIRED" and result.error_code == "BUDGET_EXCEEDED"
        assert service.mask_review.state(seed_id).draft_count == 0
        assert uow.connection.scalar(select(func.count()).select_from(uow.tables.usage_events)) == 0


async def test_late_ticket_cannot_settle_recovered_unknown_charge(api):
    from services.api.app.errors import ServiceError

    settings, _, job_id = queued(api, synthetic=False)
    job = claim_job(api.factory, settings, job_id)
    accounting = Accounting(
        api.factory,
        settings,
        job_id=job_id,
        claim_token=job["claim_token"],
        delivery=job["delivery_no"],
        purpose="SAM",
    )
    ticket = await accounting.reserve(
        CallIntent("sam-3.1", "SAM", "a" * 64, "test", Decimal("0.1"), 1)
    )
    assert await ticket.claim()
    ticket.release()
    with api.factory() as uow:
        table = uow.tables.background_jobs
        uow.connection.execute(
            table.update()
            .where(table.c.id == job_id)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        uow.commit()
    recover_jobs(api.factory, settings)
    with pytest.raises(ServiceError):
        await ticket.finish(
            "COMPLETED",
            Charge("ACTUAL", Decimal("0.001"), True, "VERIFIED_USAGE"),
            {"latency_ms": 1, "usage": {}},
            {},
        )
    with api.factory() as uow:
        usage = uow.connection.execute(select(uow.tables.usage_events)).mappings().one()
        assert usage["status"] == "UNKNOWN_CHARGE" and usage["actual_usd"] is None
        assert Budget(uow).deployment_allowance().consumed_usd == Decimal("0.1")


async def test_failed_heartbeat_cleanup_does_not_leak_or_mask_a_finalized_job(api, monkeypatch):
    from services.worker.app import jobs

    async def failed(*args):
        raise RuntimeError("sensitive driver fixture failure")

    settings, _, job_id = queued(api)
    monkeypatch.setattr(jobs, "heartbeat", failed)
    await jobs.execute_job(api.factory, settings, job_id)
    with api.factory() as uow:
        service = Service(uow, settings)
        assert service.job(job_id)["state"] == "SUCCEEDED"
