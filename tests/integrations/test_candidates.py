"""Phase04 immutable candidate/revision API roundtrips; no target/provider sockets."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from services.api.app.errors import ServiceError
from services.api.app.service import Service
from tests.integrations.test_contracts_and_jobs import contract, target

pytestmark = pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"])
PLAN = {
    "family": "background_replacement",
    "strength": 0.123456,
    "hypothesis": "Background variation",
    "prompt": "Replace only background with neutral texture.",
    "requires_muse_image": False,
    "estimated_cost_usd": 0,
}
# Full-image brightness over the reviewed full-frame mask is the only P0 family
# whose output actually differs per strength here, so candidate-slot accounting is
# exercised with genuinely distinct pixels instead of laundered duplicates.
BRIGHT = {**PLAN, "family": "brightness_shift", "target_region": "full_image", "strength": 0}


async def experiment(api, payload, cap=20):
    seed, mask, vtc = await contract(api, payload)
    config = await target(api)
    r = await api.client.post(
        "/api/v1/experiments",
        json={
            "project_id": api.project_id,
            "contract_id": vtc["id"],
            "target_config_id": config["id"],
            "limits": {**vtc["contract"]["limits"], "max_candidates": cap},
        },
        headers={"Idempotency-Key": "candidates"},
    )
    assert r.status_code == 202, r.text
    return r.json()["experiment_id"], seed, mask, vtc


async def create(api, eid, key="candidate", **changes):
    return await api.client.post(
        f"/api/v1/experiments/{eid}/mutations",
        json={"plan": PLAN, "rng_seed": 42, **changes},
        headers={"Idempotency-Key": key},
    )


def review_body(mutation, **changes):
    report = mutation["validation"]
    return {
        "prior_report_sha256": report["report_sha256"],
        "evidence_hashes": report["evidence_hashes"],
        "semantic": "consistent",
        "visibility": "clear",
        "notes": "Human verified the complete evidence.",
        **changes,
    }


async def test_candidate_snapshot_idempotency_review_history_guard(api, vtc_payload):
    eid, seed, mask, _ = await experiment(api, vtc_payload, cap=1)
    # Approval is frozen; a later current draft edit must not invalidate that history.
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.seed_masks.update()
            .where(uow.tables.seed_masks.c.id == mask)
            .values(reviewed=False, reviewer=None, reviewed_at=None, revision=2)
        )
        uow.commit()
    response = await create(api, eid)
    assert response.status_code == 201, response.text
    m = response.json()
    assert m["strength"] == 0.1235 and m["validation"]["verdict"] == "REVIEW_REQUIRED"
    assert m["validation_revision"] == 1 and m["assets"]["original"] == seed["image_asset_id"]
    assert m["validation"]["evidence_asset_ids"]
    assert (await create(api, eid)).json() == m
    assert (await create(api, eid, rng_seed=43)).status_code == 409
    # A fresh key for the identical normalized output is the same canonical
    # candidate, not a second row and not a second consumed slot.
    duplicate = await create(api, eid, key="second")
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json() == m
    with api.factory() as uow:
        with pytest.raises(ServiceError):
            Service(uow, api.settings).candidates.require_valid_candidate(UUID(m["id"]))
    body = review_body(m, notes="Bearer secret-value https://private.invalid token=secret")
    reviewed = await api.client.post(f"/api/v1/mutations/{m['id']}/review", json=body)
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["validation"]["verdict"] == "VALID"
    assert "secret-value" not in reviewed.text and "private.invalid" not in reviewed.text
    assert (
        await api.client.post(f"/api/v1/mutations/{m['id']}/review", json=body)
    ).status_code == 409
    assert (await api.client.get(f"/api/v1/mutations/{m['id']}")).json() == reviewed.json()
    history = await api.client.get(f"/api/v1/mutations/{m['id']}/history?limit=1")
    assert history.status_code == 200 and history.json()["next_cursor"]
    assert len((await api.client.get(f"/api/v1/experiments/{eid}/mutations")).json()["items"]) == 1
    with api.factory() as uow:
        s = Service(uow, api.settings)
        s.candidates.require_valid_candidate(UUID(m["id"]))
        assert (
            uow.connection.scalar(select(func.count()).select_from(uow.tables.model_observations))
            == 0
        )
        assert uow.connection.scalar(select(uow.tables.experiments.c.candidates_created)) == 1
    for table, values in [
        ("mutations", {"recipe_json": {}}),
        ("mutation_validations", {"verdict": "INVALID"}),
    ]:
        with pytest.raises(DBAPIError), api.factory() as uow:
            t = getattr(uow.tables, table)
            uow.connection.execute(t.update().values(**values))


async def test_invalid_override_requires_explicit_bound_audit(api, vtc_payload):
    eid, *_ = await experiment(api, vtc_payload)
    m = (await create(api, eid)).json()
    path = f"/api/v1/mutations/{m['id']}/review"
    r = await api.client.post(path, json=review_body(m, semantic="additional_object"))
    assert r.status_code == 200, r.text
    invalid = r.json()
    assert invalid["validation"]["verdict"] == "INVALID"
    assert (await api.client.post(path, json=review_body(invalid))).status_code == 422
    r = await api.client.post(
        path,
        json=review_body(
            invalid,
            ground_truth_audit={
                "resolved_check_codes": ["HUMAN_SEMANTICS"],
                "note": "Explicitly audited the false positive in all retained evidence.",
            },
        ),
    )
    assert r.status_code == 200 and r.json()["validation"]["verdict"] == "VALID", r.text
    with api.factory() as uow:
        rows = (
            uow.connection.execute(
                select(uow.tables.mutation_validations).order_by(
                    uow.tables.mutation_validations.c.revision
                )
            )
            .mappings()
            .all()
        )
        assert [r["revision"] for r in rows] == [1, 2, 3]
        assert rows[-1]["evidence_json"]["ground_truth_audit"]["actor"] == api.settings.owner_label
        assert rows[-1]["previous_validation_id"] == rows[-2]["id"]


async def test_candidate_concurrency_serializes_same_key_and_cap(api, vtc_payload):
    from counterforge_contracts.requests import CreateMutation

    eid, *_ = await experiment(api, vtc_payload, cap=1)
    body = CreateMutation(plan=PLAN, rng_seed=42)

    def run(_):
        with api.factory() as uow:
            m = Service(uow, api.settings).candidates.create(UUID(eid), body, "race")
            uow.commit()
            return m.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(run, range(2)))
    assert ids[0] == ids[1]


async def test_scene_rejects_invalid_plan_before_enqueue(api, vtc_payload):
    _, _, _, vtc = await experiment(api, vtc_payload)
    r = await api.client.post(
        f"/api/v1/contracts/{vtc['id']}/backgrounds",
        json={
            "plan": PLAN,
            "limits": {"max_calls": 1, "max_estimated_usd": 1, "max_runtime_seconds": 120},
        },
        headers={"Idempotency-Key": "invalid-scene"},
    )
    assert r.status_code == 422
    with api.factory() as uow:
        assert (
            uow.connection.scalar(select(func.count()).select_from(uow.tables.background_jobs)) == 0
        )


async def fixture_scene(api, vtc_payload):
    """Real fixture seed, approved five-instance masks and one settled SCENE job."""
    import io
    from datetime import UTC, datetime

    from PIL import Image, ImageDraw
    from pydantic import SecretStr

    from services.api.app.accounting import Budget
    from services.api.app.config import Settings
    from services.worker.app.fixtures import RECTANGLES, seed_bytes
    from services.worker.app.jobs import execute_job

    # Real fixture wire and accounting, with a human-approved five-instance snapshot.
    r = await api.client.post(
        f"/api/v1/projects/{api.project_id}/seeds",
        files={"file": ("seed.png", seed_bytes(), "image/png")},
        data={
            "question": vtc_payload["prompt"],
            "expected_count": 5,
            "license_ack": True,
            "label_reviewer": "owner",
        },
    )
    assert r.status_code == 201, r.text
    seed = r.json()
    with api.factory() as uow:
        s = Service(uow, api.settings)
        ids = []
        for n, box in enumerate(RECTANGLES):
            image = Image.new("L", (480, 240))
            ImageDraw.Draw(image).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=255)
            b = io.BytesIO()
            image.save(b, "PNG")
            a = s.artifacts.put(
                UUID(api.project_id), b.getvalue(), "MASK", "image/png", width=480, height=240
            )
            ids.append(str(a.id))
            uow.connection.execute(
                uow.tables.seed_masks.insert().values(
                    seed_id=UUID(seed["id"]),
                    mask_asset_id=a.id,
                    object_label="blue container",
                    object_ordinal=n,
                    geometry_json={},
                    reviewed=True,
                    reviewer="owner",
                    reviewed_at=datetime.now(UTC),
                )
            )
        Budget(uow).configure_deployment_allowance(
            max_usd=__import__("decimal").Decimal(10),
            max_calls=10,
            expires_at=datetime.now(UTC) + __import__("datetime").timedelta(hours=1),
        )
        uow.commit()
    vtc_payload.update(case_id=seed["case_key"], prompt=seed["question"], expected=seed["expected"])
    vtc_payload["protected"].update(mask_asset_ids=ids, label="blue container")
    r = await api.client.post(f"/api/v1/seeds/{seed['id']}/contracts", json=vtc_payload)
    assert r.status_code == 201, r.text
    vtc = r.json()
    config = await target(api)
    r = await api.client.post(
        "/api/v1/experiments",
        json={
            "project_id": api.project_id,
            "contract_id": vtc["id"],
            "target_config_id": config["id"],
        },
        headers={"Idempotency-Key": "fixture-experiment"},
    )
    assert r.status_code == 202, r.text
    eid = r.json()["experiment_id"]
    limits = {"max_calls": 3, "max_estimated_usd": 1, "max_runtime_seconds": 120}
    plan = {**PLAN, "requires_muse_image": True}
    r = await api.client.post(
        f"/api/v1/contracts/{vtc['id']}/backgrounds",
        json={"plan": plan, "limits": limits},
        headers={"Idempotency-Key": "scene"},
    )
    assert r.status_code == 202, r.text
    job = UUID(r.json()["job_id"])
    settings = Settings(
        artifact_root=api.root,
        owner_auth_token=api.settings.owner_auth_token,
        provider_mode="FIXTURE",
        allow_synthetic_fixtures=True,
        model_api_key=SecretStr(""),
    )
    await execute_job(api.factory, settings, job)
    with api.factory() as uow:
        s = Service(uow, settings)
        result = s.job(job)
        assert result["state"] == "SUCCEEDED", result
        assert result["experiment_id"] is None
        usage = uow.connection.execute(select(uow.tables.usage_events)).mappings().one()
        assert (
            usage["category"] == "IMAGE"
            and usage["status"] == "SETTLED"
            and usage["actual_usd"] == 0
        )
    return eid, job, settings, plan, limits


async def test_fixture_scene_accounting_and_candidate_sam_invalidate_review(
    api, vtc_payload, monkeypatch
):
    from services.worker.app.jobs import execute_job

    eid, job, settings, plan, limits = await fixture_scene(api, vtc_payload)
    r = await create(api, eid, plan=plan, background_job_id=str(job))
    assert r.status_code == 201, r.text
    m = r.json()
    r = await api.client.post(f"/api/v1/mutations/{m['id']}/review", json=review_body(m))
    assert r.status_code == 200, r.text
    assert r.json()["validation"]["verdict"] == "VALID"
    r = await api.client.post(
        f"/api/v1/mutations/{m['id']}/segment",
        json={"limits": limits},
        headers={"Idempotency-Key": "candidate-sam"},
    )
    assert r.status_code == 202, r.text
    sam_job = UUID(r.json()["job_id"])
    await execute_job(api.factory, settings, sam_job)
    with api.factory() as uow:
        s = Service(uow, settings)
        jobrow = s.job(sam_job)
        assert jobrow["state"] == "SUCCEEDED", jobrow
        assert jobrow["experiment_id"] is None
        current = s.candidates.dto(s.candidates.row(UUID(m["id"])))
        assert current.validation_revision == 3 and current.validation.verdict == "REVIEW_REQUIRED"
        assert current.validation.review_audit is None
        assert current.validation.evidence_hashes["sam"]
        assert (
            uow.connection.scalar(select(func.count()).select_from(uow.tables.model_observations))
            == 0
        )

    # SAM must discard positive approvals without laundering confirmed human FAIL.
    latest = (await api.client.get(f"/api/v1/mutations/{m['id']}")).json()
    rejected = await api.client.post(
        f"/api/v1/mutations/{m['id']}/review",
        json=review_body(latest, semantic="additional_object", visibility="destroyed"),
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["validation"]["verdict"] == "INVALID"
    queued = await api.client.post(
        f"/api/v1/mutations/{m['id']}/segment",
        json={"limits": limits},
        headers={"Idempotency-Key": "sam-after-human-failure"},
    )
    assert queued.status_code == 202, queued.text
    await execute_job(api.factory, settings, UUID(queued.json()["job_id"]))
    refreshed = (await api.client.get(f"/api/v1/mutations/{m['id']}")).json()
    assert refreshed["validation"]["verdict"] == "INVALID", refreshed
    assert {c["code"] for c in refreshed["validation"]["checks"] if c["status"] == "FAIL"} >= {
        "HUMAN_SEMANTICS",
        "HUMAN_VISIBILITY",
    }
    assert (
        await api.client.post(f"/api/v1/mutations/{m['id']}/review", json=review_body(refreshed))
    ).status_code == 422
    audited = await api.client.post(
        f"/api/v1/mutations/{m['id']}/review",
        json=review_body(
            refreshed,
            ground_truth_audit={
                "resolved_check_codes": ["HUMAN_SEMANTICS", "HUMAN_VISIBILITY"],
                "note": "Explicit audit of both retained failed human assertions.",
            },
        ),
    )
    assert audited.status_code == 200 and audited.json()["validation"]["verdict"] == "VALID", (
        audited.text
    )

    # Completion reads the latest human decision under lock, including a review
    # posted after provider dispatch and before segmentation returns. This second
    # candidate uses a deterministic plan so its pixels genuinely differ from the
    # composited scene above and it is a distinct canonical identity.
    second = await create(api, eid, key="human-during-sam")
    assert second.status_code == 201, second.text
    m2 = second.json()
    assert m2["id"] != m["id"] and m2["validation_revision"] == 1
    queued = await api.client.post(
        f"/api/v1/mutations/{m2['id']}/segment",
        json={"limits": limits},
        headers={"Idempotency-Key": "review-during-sam"},
    )
    from counterforge_meta.sam import SamSegmenter

    original_segment = SamSegmenter.segment

    async def review_during_segment(segmenter, image, concept):
        result = await original_segment(segmenter, image, concept)
        r = await api.client.post(
            f"/api/v1/mutations/{m2['id']}/review",
            json=review_body(m2, semantic="additional_object"),
        )
        assert r.status_code == 200 and r.json()["validation"]["verdict"] == "INVALID", r.text
        return result

    monkeypatch.setattr(SamSegmenter, "segment", review_during_segment)
    await execute_job(api.factory, settings, UUID(queued.json()["job_id"]))
    refreshed = (await api.client.get(f"/api/v1/mutations/{m2['id']}")).json()
    assert refreshed["validation"]["verdict"] == "INVALID", refreshed
    assert (
        await api.client.post(f"/api/v1/mutations/{m2['id']}/review", json=review_body(refreshed))
    ).status_code == 422


@pytest.mark.parametrize("role", ["candidate", "recipe", "report", "mask"])
async def test_tampered_retained_evidence_blocks_review_and_dispatch(api, vtc_payload, role):
    eid, *_ = await experiment(api, vtc_payload)
    m = (await create(api, eid)).json()
    aid = m["mask_snapshot"][0]["asset_id"] if role == "mask" else m["assets"][role]
    with api.factory() as uow:
        a = Service(uow, api.settings).asset(UUID(aid))
        path = api.root / a["storage_key"]
    path.write_bytes(b"tampered")
    r = await api.client.post(f"/api/v1/mutations/{m['id']}/review", json=review_body(m))
    assert r.status_code == 500 and r.json()["error"]["code"] == "ARTIFACT_CHECKSUM_MISMATCH"
    with api.factory() as uow:
        from services.api.app.artifacts import IntegrityError

        with pytest.raises(IntegrityError):
            Service(uow, api.settings).require_valid_candidate(UUID(m["id"]))
        assert (
            uow.connection.scalar(select(func.count()).select_from(uow.tables.mutation_validations))
            == 1
        )


async def test_pending_slots_and_runtime_bound_generation(api, vtc_payload):
    from datetime import timedelta
    from decimal import Decimal

    from services.api.app.accounting import Budget

    eid, *_ = await experiment(api, vtc_payload, cap=1)
    with api.factory() as uow:
        Budget(uow).reserve_operation(UUID(eid), "pending", "IMAGE", Decimal(0), candidate_slots=1)
        uow.commit()
    assert (await create(api, eid)).status_code == 409
    with api.factory() as uow:
        Budget(uow).void_operation("pending")
        uow.connection.execute(
            uow.tables.experiments.update().values(started_at=func.now() - timedelta(hours=3))
        )
        uow.commit()
    assert (await create(api, eid)).status_code == 409
    with api.factory() as uow:
        assert uow.connection.scalar(select(uow.tables.experiments.c.candidates_created)) == 0
        assert uow.connection.scalar(select(func.count()).select_from(uow.tables.mutations)) == 0


async def test_distinct_candidate_requests_race_for_one_slot(api, vtc_payload):
    from counterforge_contracts.requests import CreateMutation

    eid, *_ = await experiment(api, vtc_payload, cap=1)

    def run(n):
        try:
            with api.factory() as uow:
                m = Service(uow, api.settings).candidates.create(
                    UUID(eid),
                    CreateMutation(plan={**BRIGHT, "strength": 0.2 * n}, rng_seed=n),
                    f"cap-{n}",
                )
                uow.commit()
                return str(m.id)
        except ServiceError as e:
            return str(e.code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert results.count("BUDGET_EXCEEDED") == 1, results


@pytest.mark.parametrize("state", ["PAUSED", "COMPLETED", "BUDGET_EXHAUSTED", "FAILED"])
async def test_nonrunnable_experiment_never_generates(api, vtc_payload, state):
    eid, *_ = await experiment(api, vtc_payload)
    with api.factory() as uow:
        uow.connection.execute(uow.tables.experiments.update().values(state=state))
        uow.commit()
    assert (await create(api, eid)).status_code == 409
    with api.factory() as uow:
        assert uow.connection.scalar(select(func.count()).select_from(uow.tables.mutations)) == 0


async def test_candidate_api_ownership_and_strict_review_actor(api, vtc_payload):
    eid, *_ = await experiment(api, vtc_payload)
    m = (await create(api, eid)).json()
    r = await api.client.post(
        f"/api/v1/mutations/{m['id']}/review", json={**review_body(m), "actor": "spoofed"}
    )
    assert r.status_code == 422
    assert (
        await api.client.get(f"/api/v1/mutations/{m['id']}", headers={"Authorization": ""})
    ).status_code == 401
    with api.factory() as uow:
        uow.connection.execute(uow.tables.projects.update().values(owner_label="another-owner"))
        uow.commit()
    assert (await api.client.get(f"/api/v1/mutations/{m['id']}")).status_code == 403
    assert (await create(api, eid)).status_code == 403


@pytest.mark.parametrize("kind", ["SCENE", "MUTATION_SAM"])
@pytest.mark.parametrize("dispatched", [False, True])
async def test_preparation_recovery_voids_or_quarantines_without_paid_retry(api, kind, dispatched):
    from datetime import timedelta
    from decimal import Decimal

    from counterforge_meta.usage import CallIntent

    from services.api.app.provider_accounting import Accounting
    from services.worker.app.jobs import claim_job, fenced, recover_jobs
    from tests.integrations.test_provider_jobs import queued

    settings, _, jid = queued(api)
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.background_jobs.update()
            .where(uow.tables.background_jobs.c.id == jid)
            .values(kind=kind)
        )
        uow.commit()
    job = claim_job(api.factory, settings, jid)
    a = Accounting(
        api.factory,
        settings,
        job_id=jid,
        claim_token=job["claim_token"],
        delivery=job["delivery_no"],
        purpose="IMAGE" if kind == "SCENE" else "SAM",
        synthetic=True,
    )
    ticket = await a.reserve(
        CallIntent(
            "muse-image-1.0" if kind == "SCENE" else "sam-3.1",
            "IMAGE" if kind == "SCENE" else "SAM",
            "a" * 64,
            "fixture",
            Decimal(0),
            1,
        )
    )
    if dispatched:
        assert await ticket.claim()
        ticket.release()
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.background_jobs.update().values(
                lease_expires_at=func.now() - timedelta(seconds=1)
            )
        )
        uow.commit()
    recovered = recover_jobs(api.factory, settings)
    assert (jid in recovered) is not dispatched
    with api.factory() as uow:
        assert fenced(uow, job) is None
        event = uow.connection.execute(select(uow.tables.usage_events)).mappings().one()
        assert event["status"] == ("UNKNOWN_CHARGE" if dispatched else "VOID")
        assert Service(uow, settings).job(jid)["state"] == (
            "REVIEW_REQUIRED" if dispatched else "QUEUED"
        )


async def test_repeated_review_redacts_before_sealing_and_keeps_history_valid(api, vtc_payload):
    from counterforge_contracts.responses import ValidationReport
    from counterforge_verification.invariants import report_digest

    eid, *_ = await experiment(api, vtc_payload)
    m = (await create(api, eid)).json()
    for _ in range(2):
        r = await api.client.post(
            f"/api/v1/mutations/{m['id']}/review",
            json=review_body(
                m,
                notes="Verified "
                + api.settings.owner_auth_token.get_secret_value()
                + " https://secret.invalid",
            ),
        )
        assert r.status_code == 200, r.text
        m = r.json()
        report = ValidationReport.model_validate(m["validation"])
        assert report.report_sha256 == report_digest(report)
        assert (
            api.settings.owner_auth_token.get_secret_value() not in r.text
            and "secret.invalid" not in r.text
        )
    history = (await api.client.get(f"/api/v1/mutations/{m['id']}/history")).json()["items"]
    assert [h["revision"] for h in history] == [1, 2, 3]
    assert all(
        ValidationReport.model_validate(h["report"]).report_sha256
        == report_digest(ValidationReport.model_validate(h["report"]))
        for h in history
    )


async def test_scene_job_must_match_contract_plan_source_and_project(api, vtc_payload):
    from copy import deepcopy

    from counterforge_contracts.requests import BackgroundRequest

    eid, _, _, vtc = await experiment(api, vtc_payload)
    plan = {**PLAN, "requires_muse_image": True}
    body = BackgroundRequest(
        plan=plan, limits={"max_calls": 1, "max_estimated_usd": 1, "max_runtime_seconds": 120}
    )
    with api.factory() as uow:
        s = Service(uow, api.settings)
        first = s.candidates.request_background(UUID(vtc["id"]), body, "same-contract")
        uow.connection.execute(
            uow.tables.background_jobs.update()
            .where(uow.tables.background_jobs.c.id == first.job_id)
            .values(state="SUCCEEDED", result_json={"synthetic": True})
        )
        uow.commit()
    # A SUCCEEDED label alone cannot make changed plan or seed references match.
    r = await create(api, eid, plan={**plan, "strength": 0.3}, background_job_id=str(first.job_id))
    assert r.status_code == 422
    other_project = (
        await api.client.post("/api/v1/projects", json={"name": "Foreign background"})
    ).json()["id"]
    from tests.integrations.test_contracts_and_jobs import contract

    _, _, other = await contract(api, deepcopy(vtc_payload), project_id=other_project)
    with api.factory() as uow:
        s = Service(uow, api.settings)
        foreign = s.candidates.request_background(UUID(other["id"]), body, "foreign")
        uow.connection.execute(
            uow.tables.background_jobs.update()
            .where(uow.tables.background_jobs.c.id == foreign.job_id)
            .values(state="SUCCEEDED", result_json={"synthetic": True})
        )
        uow.commit()
    r = await create(api, eid, plan=plan, background_job_id=str(foreign.job_id))
    assert r.status_code == 422
    with api.factory() as uow:
        assert uow.connection.scalar(select(func.count()).select_from(uow.tables.usage_events)) == 0
        assert uow.connection.scalar(select(func.count()).select_from(uow.tables.mutations)) == 0


async def test_identical_output_is_one_canonical_candidate_and_cannot_wash_invalid(
    api, vtc_payload
):
    """A new key/seed for the same pixels must not mint fresh human gates."""
    eid, job, _, plan, _ = await fixture_scene(api, vtc_payload)
    first = await create(api, eid, key="scene-a", plan=plan, background_job_id=str(job))
    assert first.status_code == 201, first.text
    a = first.json()
    # B is requested before A is rejected: a pre-created duplicate must already be
    # the same identity, so it cannot survive A's later rejection.
    early = await create(
        api, eid, key="scene-b", plan=plan, rng_seed=43, background_job_id=str(job)
    )
    assert early.status_code == 201, early.text
    assert early.json()["id"] == a["id"] and early.json()["recipe"]["rng_seed"] == 42
    r = await api.client.post(
        f"/api/v1/mutations/{a['id']}/review", json=review_body(a, semantic="additional_object")
    )
    assert r.status_code == 200, r.text
    assert r.json()["validation"]["verdict"] == "INVALID"
    laundered = await create(
        api, eid, key="scene-c", plan=plan, rng_seed=99, background_job_id=str(job)
    )
    assert laundered.status_code == 201, laundered.text
    washed = laundered.json()
    assert washed["id"] == a["id"] and washed["validation"]["verdict"] == "INVALID"
    assert washed["validation_revision"] == 2 and washed["recipe"]["rng_seed"] == 42
    # The preserved rejection still requires an explicit bound audit to promote.
    assert (
        await api.client.post(f"/api/v1/mutations/{a['id']}/review", json=review_body(washed))
    ).status_code == 422
    # A bound key with a changed body stays a conflict; an exact replay resolves.
    assert (
        await create(api, eid, key="scene-c", plan=plan, rng_seed=100, background_job_id=str(job))
    ).status_code == 409
    replay = await create(
        api, eid, key="scene-c", plan=plan, rng_seed=99, background_job_id=str(job)
    )
    assert replay.status_code == 201 and replay.json()["id"] == a["id"]
    with api.factory() as uow:
        aliases = uow.tables.mutation_request_aliases
        assert uow.connection.scalar(select(func.count()).select_from(uow.tables.mutations)) == 1
        assert uow.connection.scalar(select(uow.tables.experiments.c.candidates_created)) == 1
        rows = (
            uow.connection.execute(select(aliases).order_by(aliases.c.created_at, aliases.c.id))
            .mappings()
            .all()
        )
        assert [row["idempotency_key"] for row in rows] == ["scene-a", "scene-b", "scene-c"]
        assert {row["mutation_id"] for row in rows} == {UUID(a["id"])}
        assert len({row["request_sha256"] for row in rows}) == 3
        assert [row["request_json"]["rng_seed"] for row in rows] == [42, 43, 99]
    # Each requested intent stays append-only evidence of what was asked for.
    with pytest.raises(DBAPIError), api.factory() as uow:
        uow.connection.execute(
            uow.tables.mutation_request_aliases.update().values(idempotency_key="rewritten")
        )


async def test_reapproved_identical_mask_keeps_paid_background_usable(api, vtc_payload):
    """Approval provenance may advance; identical reviewed content stays usable."""
    from datetime import UTC, datetime, timedelta

    from counterforge_mutations.compositor import encode_png
    from PIL import Image

    _, mask, vtc = await contract(api, vtc_payload)
    plan = {**PLAN, "requires_muse_image": True}
    r = await api.client.post(
        f"/api/v1/contracts/{vtc['id']}/backgrounds",
        json={
            "plan": plan,
            "limits": {"max_calls": 1, "max_estimated_usd": 1, "max_runtime_seconds": 120},
        },
        headers={"Idempotency-Key": "reapproved-scene"},
    )
    assert r.status_code == 202, r.text
    job = UUID(r.json()["job_id"])
    # The exact same reviewed mask bytes are approved again after the paid request.
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.seed_masks.update()
            .where(uow.tables.seed_masks.c.id == mask)
            .values(
                reviewer="Second reviewer",
                reviewed_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        uow.commit()
    config = await target(api)
    r = await api.client.post(
        "/api/v1/experiments",
        json={
            "project_id": api.project_id,
            "contract_id": vtc["id"],
            "target_config_id": config["id"],
        },
        headers={"Idempotency-Key": "reapproved"},
    )
    assert r.status_code == 202, r.text
    eid = r.json()["experiment_id"]
    with api.factory() as uow:
        s = Service(uow, api.settings)
        asset = s.artifacts.put(
            UUID(api.project_id),
            encode_png(Image.new("RGB", (12, 8), "blue")),
            "OTHER",
            "image/png",
            width=12,
            height=8,
        )
        uow.connection.execute(
            uow.tables.background_jobs.update()
            .where(uow.tables.background_jobs.c.id == job)
            .values(
                state="SUCCEEDED",
                result_json={
                    "synthetic": True,
                    "background_asset_id": str(asset.id),
                    "background_sha256": asset.sha256,
                    "original_background_asset_id": str(asset.id),
                    "original_background_sha256": asset.sha256,
                },
            )
        )
        uow.commit()
    created = await create(api, eid, plan=plan, background_job_id=str(job))
    assert created.status_code == 201, created.text
    m = created.json()
    assert m["synthetic"] is True and m["assets"]["provider_background"] == str(asset.id)
    # Both approval records survive: the experiment keeps the newer approval and
    # the paid job keeps the snapshot it was actually generated against.
    assert m["mask_snapshot"][0]["reviewer"] == "Second reviewer"
    with api.factory() as uow:
        s = Service(uow, api.settings)
        request = s.job(job)["request_json"]
        assert request["snapshot"]["masks"][0]["reviewer"] == "Mask reviewer"
        row = s.candidates.row(UUID(m["id"]))
        assert row["evidence_json"]["background_snapshot_sha256"] == request["snapshot_sha256"]


async def test_stored_verdict_and_verifier_version_are_pinned_without_dispatch(api, vtc_payload):
    """A sealed report never outranks its own gates or an unsupported version."""
    from counterforge_contracts.responses import ValidationReport
    from counterforge_contracts.versions import ValidityVerdict
    from counterforge_verification.invariants import report_digest

    from services.api.app.artifacts import IntegrityError
    from services.api.app.service import canonical_json

    eid, *_ = await experiment(api, vtc_payload)
    m = (await create(api, eid)).json()
    assert m["validation"]["verdict"] == "REVIEW_REQUIRED"
    with api.factory() as uow:
        s = Service(uow, api.settings)
        t = uow.tables.mutation_validations
        row = uow.connection.execute(select(t)).mappings().one()
        evidence = dict(row["evidence_json"])
        forged = ValidationReport.model_validate(evidence["report"])
        # Self-consistently sealed, but the mandatory human gates stay UNKNOWN.
        forged.verdict = ValidityVerdict.VALID
        forged.report_sha256 = report_digest(forged)
        asset = s.artifacts.put(
            UUID(api.project_id),
            canonical_json(forged.model_dump(mode="json")),
            "OTHER",
            "application/json",
        )
        uow.connection.execute(
            t.insert().values(
                mutation_id=UUID(m["id"]),
                verifier_version=forged.verifier_version,
                policy_version=forged.policy_version,
                verdict="VALID",
                evidence_json={
                    **evidence,
                    "report": forged.model_dump(mode="json"),
                    "report_asset_id": str(asset.id),
                    "report_asset_sha256": asset.sha256,
                },
            )
        )
        uow.commit()
    with api.factory() as uow:
        with pytest.raises(IntegrityError):
            Service(uow, api.settings).require_valid_candidate(UUID(m["id"]))
    # An older sealed verifier policy is unresolved evidence, not a 500.
    with api.factory() as uow:
        t = uow.tables.mutation_validations
        evidence = (
            uow.connection.execute(select(t).order_by(t.c.revision.desc()).limit(1))
            .mappings()
            .one()["evidence_json"]
        )
        uow.connection.execute(
            t.insert().values(
                mutation_id=UUID(m["id"]),
                verifier_version="phase04-0",
                policy_version="strict-binary-human-gates-0",
                verdict="REVIEW_REQUIRED",
                evidence_json={
                    **evidence,
                    "report": {
                        **evidence["report"],
                        "verifier_version": "phase04-0",
                        "policy_version": "strict-binary-human-gates-0",
                        "verdict": "REVIEW_REQUIRED",
                    },
                },
            )
        )
        uow.commit()
    for path, body in [
        (f"/api/v1/mutations/{m['id']}/review", review_body(m)),
        (
            f"/api/v1/mutations/{m['id']}/segment",
            {"limits": {"max_calls": 1, "max_estimated_usd": 1, "max_runtime_seconds": 120}},
        ),
    ]:
        r = await api.client.post(path, json=body, headers={"Idempotency-Key": "pinned"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "REVIEW_REQUIRED", r.text
    with api.factory() as uow:
        assert (
            uow.connection.scalar(select(func.count()).select_from(uow.tables.background_jobs)) == 0
        )
        with pytest.raises(ServiceError) as error:
            Service(uow, api.settings).require_valid_candidate(UUID(m["id"]))
        assert error.value.code == "REVIEW_REQUIRED"


async def test_legacy_and_unvalidated_candidate_reads_are_safe_and_never_dispatch(api, vtc_payload):
    eid, *_ = await experiment(api, vtc_payload)
    with api.factory() as uow:
        mid = uow.connection.scalar(
            uow.tables.mutations.insert()
            .values(
                experiment_id=UUID(eid),
                family="brightness_shift",
                strength=0.1,
                generator="DETERMINISTIC",
                recipe_json={},
                state="PLANNED",
            )
            .returning(uow.tables.mutations.c.id)
        )
        uow.commit()
    r = await api.client.get(f"/api/v1/experiments/{eid}/mutations")
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["validation"] is None
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.mutation_validations.insert().values(
                mutation_id=mid,
                verifier_version="legacy",
                verdict="VALID",
                evidence_json={"private_url": "DO_NOT_LEAK"},
            )
        )
        uow.commit()
    r = await api.client.get(f"/api/v1/mutations/{mid}/history")
    assert r.status_code == 200, r.text
    assert (
        r.json()["items"][0]["report"] is None and r.json()["items"][0]["report_asset_id"] is None
    )
    assert "DO_NOT_LEAK" not in r.text
    with api.factory() as uow:
        with pytest.raises(ServiceError) as error:
            Service(uow, api.settings).require_valid_candidate(mid)
        assert error.value.code == "REVIEW_REQUIRED"
