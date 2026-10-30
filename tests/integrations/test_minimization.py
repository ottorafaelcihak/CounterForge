"""Refinement is a fenced followup, with fresh independent human gates."""

import io
import json
from datetime import timedelta
from hashlib import sha256
from uuid import UUID
from zipfile import ZipFile

import pytest
from sqlalchemy import func, select

from services.worker.app.jobs import execute_job
from tests.integrations.test_candidates import review_body
from tests.integrations.test_experiment_pipeline import replies
from tests.integrations.test_exports import stable

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


async def source(api, payload, monkeypatch):
    api.settings.provider_mode = "FIXTURE"
    api.settings.allow_synthetic_fixtures = True
    fid, original = await stable(api, payload, monkeypatch)
    with api.factory() as uow:
        e = uow.tables.experiments
        uow.connection.execute(
            e.update().values(state="COMPLETED", runner_token=None, dispatch_pending=False)
        )
        uow.commit()
    return fid, original


async def test_minimize_requires_review_and_explicit_resume_then_exports_child(
    api, vtc_payload, monkeypatch
):
    fid, original = await source(api, vtc_payload, monkeypatch)
    from tests.integrations.test_exports import request

    original_export = UUID((await request(api, fid)).json()["export_id"])
    await execute_job(api.factory, api.settings, original_export)
    original_bundle = (await api.client.get(f"/api/v1/exports/{original_export}/download")).content
    original_candidate = (await api.client.get(f"/api/v1/mutations/{original['id']}")).json()
    r = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 1},
        headers={"Idempotency-Key": "min-once"},
    )
    assert r.status_code == 202, r.text
    jid = UUID(r.json()["job_id"])
    await execute_job(api.factory, api.settings, jid)
    job = (await api.client.get(f"/api/v1/jobs/{jid}")).json()
    assert job["state"] == "REVIEW_REQUIRED", job
    result = job["result"]["minimization"]
    child_id = result["probes"][0]["mutation_id"]
    child = (await api.client.get(f"/api/v1/mutations/{child_id}")).json()
    assert child["parent_mutation_id"] == original["id"]
    assert child["minimization_job_id"] == str(jid)
    assert child["strength"] == 0.25
    assert (await api.client.post(f"/api/v1/jobs/{jid}/resume")).status_code == 409
    reviewed = await api.client.post(
        f"/api/v1/mutations/{child_id}/review", json=review_body(child)
    )
    assert reviewed.status_code == 200, reviewed.text
    assert (await api.client.get(f"/api/v1/jobs/{jid}")).json()["state"] == "REVIEW_REQUIRED"
    assert (await api.client.post(f"/api/v1/jobs/{jid}/resume")).status_code == 202
    replies(monkeypatch, [0, 0, 0, 0])
    await execute_job(api.factory, api.settings, jid)
    job = (await api.client.get(f"/api/v1/jobs/{jid}")).json()
    assert job["state"] == "SUCCEEDED", job
    result = job["result"]["minimization"]
    assert result["best_mutation_id"] == child_id
    assert result["best_finding_id"] != fid
    assert result["probes"][0]["confirmation_outcomes"] == ["INCORRECT"] * 3
    assert result["usage"]["calls"] == result["usage"]["target_calls"] == 4
    exported = await api.client.post(
        f"/api/v1/findings/{result['best_finding_id']}/exports",
        headers={"Idempotency-Key": "reduced-export"},
    )
    assert exported.status_code == 202, exported.text
    await execute_job(api.factory, api.settings, UUID(exported.json()["export_id"]))
    ready = (await api.client.get(exported.json()["links"]["self"])).json()
    assert ready["state"] == "READY", ready
    child_bundle = (await api.client.get(ready["links"]["download"])).content
    from counterforge_regression.bundle import verify_bundle

    verify_bundle(child_bundle)
    with ZipFile(io.BytesIO(child_bundle)) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        recipe = json.loads(bundle.read(manifest["files"]["recipe"]))
        assert manifest["parent_mutation_id"] == original["id"]
        assert manifest["parent_finding_id"] == fid
        assert manifest["minimization_job_id"] == str(jid)
        assert manifest["finding_id"] == result["best_finding_id"]
        assert recipe["rng_seed"] == original_candidate["recipe"]["rng_seed"]
        assert recipe["source_sha256"] == original_candidate["recipe"]["source_sha256"]
        assert (
            sha256(bundle.read(manifest["files"]["failing"])).hexdigest() == recipe["output_sha256"]
        )
    assert (
        await api.client.get(f"/api/v1/exports/{original_export}/download")
    ).content == original_bundle
    assert (
        await api.client.get(f"/api/v1/mutations/{original['id']}")
    ).json() == original_candidate
    with api.factory() as uow:
        e = uow.connection.execute(select(uow.tables.experiments)).mappings().one()
        assert e["state"] == "COMPLETED" and e["candidates_created"] == 2
        assert e["calls_consumed"] == 11
    assert len((await api.client.get(f"/api/v1/findings/{fid}/minimizations")).json()["items"]) == 1


@pytest.mark.parametrize("change", ["runtime", "state", "active", "candidates"])
async def test_source_original_limits_and_terminal_fence_remain_ceilings(
    api, vtc_payload, monkeypatch, change
):
    fid, original = await source(api, vtc_payload, monkeypatch)
    with api.factory() as uow:
        e = uow.tables.experiments
        values = (
            {"started_at": func.now() - timedelta(hours=3)}
            if change == "runtime"
            else {"state": "CANCELLED"}
            if change == "state"
            else {"dispatch_pending": True}
            if change == "active"
            else {"max_candidates": 1}
        )
        uow.connection.execute(e.update().values(**values))
        uow.commit()
    r = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 1},
        headers={"Idempotency-Key": "blocked"},
    )
    assert r.status_code == 409, r.text


async def test_reviewed_invalid_probe_can_finish_without_calls_and_cancel_releases_source(
    api, vtc_payload, monkeypatch
):
    fid, original = await source(api, vtc_payload, monkeypatch)
    r = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 2},
        headers={"Idempotency-Key": "invalid"},
    )
    jid = UUID(r.json()["job_id"])
    await execute_job(api.factory, api.settings, jid)
    result = (await api.client.get(f"/api/v1/jobs/{jid}")).json()["result"]["minimization"]
    mid = result["probes"][-1]["mutation_id"]
    child = (await api.client.get(f"/api/v1/mutations/{mid}")).json()
    assert (
        await api.client.post(
            f"/api/v1/mutations/{mid}/review", json=review_body(child, semantic="additional_object")
        )
    ).status_code == 200
    resumed = await api.client.post(f"/api/v1/jobs/{jid}/resume")
    assert resumed.status_code == 202, resumed.text
    await execute_job(api.factory, api.settings, jid)
    job = (await api.client.get(f"/api/v1/jobs/{jid}")).json()
    assert job["result"]["minimization"]["stop_reason"] == "INVALID_PROBE"
    assert job["result"]["minimization"]["best_mutation_id"] == original["id"]
    with api.factory() as uow:
        assert uow.connection.scalar(select(uow.tables.experiments.c.calls_consumed)) == 7


async def test_owner_cancel_releases_source(api, vtc_payload, monkeypatch):
    fid, original = await source(api, vtc_payload, monkeypatch)
    new = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 1},
        headers={"Idempotency-Key": "cancel-me"},
    )
    second = new.json()["job_id"]
    await execute_job(api.factory, api.settings, UUID(second))
    cancelled = await api.client.post(f"/api/v1/jobs/{second}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["result"]["minimization"]["stop_reason"] == "OWNER_STOP"


async def reviewed_probe(api, jid, monkeypatch, values):
    job = (await api.client.get(f"/api/v1/jobs/{jid}")).json()
    mid = job["result"]["minimization"]["probes"][-1]["mutation_id"]
    child = (await api.client.get(f"/api/v1/mutations/{mid}")).json()
    r = await api.client.post(f"/api/v1/mutations/{mid}/review", json=review_body(child))
    assert r.status_code == 200, r.text
    r = await api.client.post(f"/api/v1/jobs/{jid}/resume")
    assert r.status_code == 202, r.text
    replies(monkeypatch, values)
    await execute_job(api.factory, api.settings, UUID(str(jid)))
    return (await api.client.get(f"/api/v1/jobs/{jid}")).json()


@pytest.mark.parametrize(
    "outcomes,outcome", [([1], "PASSING"), ([0, 0, 1, 0], "INTERMITTENT"), (["UNKNOWN"], "UNKNOWN")]
)
async def test_boundary_and_intermittent_probes_preserve_original_best(
    api, vtc_payload, monkeypatch, outcomes, outcome
):
    fid, original = await source(api, vtc_payload, monkeypatch)
    response = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 1},
        headers={"Idempotency-Key": "boundary"},
    )
    jid = response.json()["job_id"]
    await execute_job(api.factory, api.settings, UUID(jid))
    job = await reviewed_probe(api, jid, monkeypatch, outcomes)
    result = job["result"]["minimization"]
    assert result["probes"][0]["outcome"] == outcome
    assert result["best_mutation_id"] == original["id"]
    assert result["best_finding_id"] == fid
    with api.factory() as uow:
        f = (
            uow.connection.execute(
                select(uow.tables.findings).where(uow.tables.findings.c.id == UUID(fid))
            )
            .mappings()
            .one()
        )
        assert f["status"] == "STABLE" and f["confirmation_failures"] == 3


async def test_binary_search_retains_stable_best_and_passing_neighbor(
    api, vtc_payload, monkeypatch
):
    fid, original = await source(api, vtc_payload, monkeypatch)
    response = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 2},
        headers={"Idempotency-Key": "two-probes"},
    )
    jid = response.json()["job_id"]
    await execute_job(api.factory, api.settings, UUID(jid))
    first = await reviewed_probe(api, jid, monkeypatch, [0, 0, 0, 0])
    result = first["result"]["minimization"]
    assert first["state"] == "REVIEW_REQUIRED"
    assert [p["strength"] for p in result["probes"]] == [0.25, 0.375]
    best = result["best_mutation_id"]
    assert best != original["id"]
    second = await reviewed_probe(api, jid, monkeypatch, [1])
    result = second["result"]["minimization"]
    assert result["best_mutation_id"] == best
    assert [p["outcome"] for p in result["probes"]] == ["STABLE", "PASSING"]
    experiment = (await api.client.get(f"/api/v1/experiments/{original['experiment_id']}")).json()
    assert experiment["metrics"]["generated_candidates"] == 1
    assert experiment["metrics"]["stable_findings"] == 1
    assert len(experiment["planner_history"]) == 1


async def test_queue_capacity_and_no_automatic_resume_or_duplicate_dispatch(
    api, vtc_payload, monkeypatch
):
    fid, _ = await source(api, vtc_payload, monkeypatch)
    api.settings.max_pending_jobs = 1
    response = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 1},
        headers={"Idempotency-Key": "cap"},
    )
    assert response.status_code == 202, response.text
    jid = UUID(response.json()["job_id"])
    conflict = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 2},
        headers={"Idempotency-Key": "cap"},
    )
    assert conflict.status_code == 409
    concurrent = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 1},
        headers={"Idempotency-Key": "another"},
    )
    assert concurrent.status_code == 409
    await execute_job(api.factory, api.settings, jid)
    await execute_job(api.factory, api.settings, jid)
    with api.factory() as uow:
        assert uow.connection.scalar(select(func.count()).select_from(uow.tables.mutations)) == 2
        assert uow.connection.scalar(select(uow.tables.experiments.c.calls_consumed)) == 7


@pytest.mark.parametrize(
    "cap,reason",
    [
        ("calls", "CONFIRMATION_TARGET_CALL_CAP"),
        ("runtime", "CONFIRMATION_RUNTIME_CAP"),
    ],
)
async def test_caps_rechecked_after_human_review_keep_original_and_make_no_calls(
    api, vtc_payload, monkeypatch, cap, reason
):
    fid, original = await source(api, vtc_payload, monkeypatch)
    response = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 1},
        headers={"Idempotency-Key": "review-budget"},
    )
    jid = UUID(response.json()["job_id"])
    await execute_job(api.factory, api.settings, jid)
    with api.factory() as uow:
        e = uow.tables.experiments
        values = (
            {"max_target_calls": 7}
            if cap == "calls"
            else {"started_at": func.now() - timedelta(seconds=1100)}
        )
        uow.connection.execute(e.update().values(**values))
        uow.commit()
    job = await reviewed_probe(api, jid, monkeypatch, [])
    result = job["result"]["minimization"]
    assert result["stop_reason"] == reason
    assert result["best_mutation_id"] == original["id"]
    assert result["usage"]["target_calls"] == 0
    with api.factory() as uow:
        assert uow.connection.scalar(select(uow.tables.experiments.c.calls_consumed)) == 7


@pytest.mark.parametrize(
    "strength,reason,probes", [(0, "NO_REMOVABLE_ELEMENT", 0), (0.3, "NO_NEW_IMAGE", 1)]
)
async def test_clutter_floor_and_alias_preserve_canonical_source_without_provider_work(
    api, vtc_payload, monkeypatch, strength, reason, probes
):
    from tests.integrations import test_experiment_pipeline as pipeline
    from tests.integrations.test_candidates import PLAN

    monkeypatch.setattr(
        pipeline, "BRIGHT", {**PLAN, "family": "background_clutter", "strength": strength}
    )
    fid, original = await source(api, vtc_payload, monkeypatch)
    retained_source = (await api.client.get(f"/api/v1/mutations/{original['id']}")).json()
    response = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 8},
        headers={"Idempotency-Key": "one-layer"},
    )
    assert response.status_code == 202, response.text
    jid = UUID(response.json()["job_id"])
    await execute_job(api.factory, api.settings, jid)
    result = (await api.client.get(f"/api/v1/jobs/{jid}")).json()["result"]["minimization"]
    assert result["stop_reason"] == reason
    assert result["probes_used"] == probes
    if probes:
        assert result["probes"][0]["outcome"] == "ALIASED"
        assert result["probes"][0]["mutation_id"] == original["id"]
    assert result["best_mutation_id"] == original["id"]
    assert result["usage"]["calls"] == 0
    assert (await api.client.get(f"/api/v1/mutations/{original['id']}")).json() == retained_source
    with api.factory() as uow:
        assert uow.connection.scalar(select(uow.tables.experiments.c.candidates_created)) == 1


async def test_recovery_preserves_review_gate_and_fences_old_delivery(
    api, vtc_payload, monkeypatch
):
    from services.worker.app.jobs import claim_job, fenced, recover_jobs

    fid, _ = await source(api, vtc_payload, monkeypatch)
    response = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 1},
        headers={"Idempotency-Key": "recover-min"},
    )
    jid = UUID(response.json()["job_id"])
    old = claim_job(api.factory, api.settings, jid)
    with api.factory() as uow:
        j = uow.tables.background_jobs
        uow.connection.execute(
            j.update()
            .where(j.c.id == jid)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        uow.commit()
    assert jid in recover_jobs(api.factory, api.settings)
    with api.factory() as uow:
        assert fenced(uow, old) is None
    await execute_job(api.factory, api.settings, jid)
    before = (await api.client.get(f"/api/v1/jobs/{jid}")).json()
    assert before["state"] == "REVIEW_REQUIRED" and before["finished_at"] is None
    assert jid not in recover_jobs(api.factory, api.settings)
    await execute_job(api.factory, api.settings, jid)
    assert (await api.client.get(f"/api/v1/jobs/{jid}")).json() == before


async def test_supplied_clutter_background_is_explicitly_unsupported_without_regeneration(
    api, vtc_payload, monkeypatch
):
    from services.worker.app.experiments import execute_experiment
    from tests.integrations import test_candidates as candidates
    from tests.integrations.test_experiment_pipeline import fixture_settings

    api.settings.provider_mode = "FIXTURE"
    api.settings.allow_synthetic_fixtures = True
    monkeypatch.setattr(candidates, "PLAN", {**candidates.PLAN, "family": "background_clutter"})
    eid, background, _, plan, _ = await candidates.fixture_scene(api, vtc_payload)
    created = await candidates.create(api, eid, plan=plan, background_job_id=str(background))
    assert created.status_code == 201, created.text
    child = created.json()
    reviewed = await api.client.post(
        f"/api/v1/mutations/{child['id']}/review", json=review_body(child)
    )
    assert reviewed.status_code == 200, reviewed.text
    for state, outcomes in (("BASELINE_VALIDATING", [5, 5, 5]), ("EVALUATING", [0, 0, 0, 0])):
        with api.factory() as uow:
            e = uow.tables.experiments
            uow.connection.execute(e.update().where(e.c.id == UUID(eid)).values(state=state))
            uow.commit()
        replies(monkeypatch, outcomes)
        for _ in outcomes:
            await execute_experiment(api.factory, fixture_settings(api), UUID(eid))
    with api.factory() as uow:
        fid = uow.connection.scalar(select(uow.tables.findings.c.id))
        e = uow.tables.experiments
        uow.connection.execute(
            e.update()
            .where(e.c.id == UUID(eid))
            .values(state="COMPLETED", runner_token=None, dispatch_pending=False)
        )
        uow.commit()
    response = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 8},
        headers={"Idempotency-Key": "supplied-clutter"},
    )
    assert response.status_code == 202, response.text
    jid = UUID(response.json()["job_id"])
    await execute_job(api.factory, api.settings, jid)
    result = (await api.client.get(f"/api/v1/jobs/{jid}")).json()["result"]["minimization"]
    assert result["stop_reason"] == "GENERATIVE_BACKGROUND"
    assert result["probes_used"] == 0 and result["best_mutation_id"] == child["id"]
    assert result["usage"]["calls"] == 0


async def test_dispatched_probe_recovery_retains_typed_result_without_retry(
    api, vtc_payload, monkeypatch
):
    from decimal import Decimal

    from services.api.app.accounting import Budget
    from services.worker.app.jobs import claim_job, fenced, recover_jobs

    fid, original = await source(api, vtc_payload, monkeypatch)
    response = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 1},
        headers={"Idempotency-Key": "claimed-min"},
    )
    jid = UUID(response.json()["job_id"])
    await execute_job(api.factory, api.settings, jid)
    job = (await api.client.get(f"/api/v1/jobs/{jid}")).json()
    mid = job["result"]["minimization"]["probes"][-1]["mutation_id"]
    mutation = (await api.client.get(f"/api/v1/mutations/{mid}")).json()
    assert (
        await api.client.post(f"/api/v1/mutations/{mid}/review", json=review_body(mutation))
    ).status_code == 200
    assert (await api.client.post(f"/api/v1/jobs/{jid}/resume")).status_code == 202
    old = claim_job(api.factory, api.settings, jid)
    with api.factory() as uow:
        budget = Budget(uow)
        key = f"{jid}:claimed-probe"
        from services.api.app.errors import ServiceError

        # Positive provider upper bounds must obey the original dollar ceiling,
        # even though fixture wire calls themselves are correctly free.
        with (
            pytest.raises(ServiceError, match="estimated spend cap"),
            uow.connection.begin_nested(),
        ):
            budget.reserve_followup_operation(
                UUID(original["experiment_id"]),
                jid,
                f"{jid}:over-dollar-cap",
                "TARGET",
                Decimal("10.01"),
                target_calls=1,
            )
        budget.reserve_followup_operation(
            UUID(original["experiment_id"]), jid, key, "TARGET", Decimal(0), target_calls=1
        )
        assert budget.begin_operation(key)
        j = uow.tables.background_jobs
        uow.connection.execute(
            j.update()
            .where(j.c.id == jid)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        uow.commit()
    assert jid not in recover_jobs(api.factory, api.settings)
    with api.factory() as uow:
        assert fenced(uow, old) is None
    retained = (await api.client.get(f"/api/v1/jobs/{jid}")).json()
    result = retained["result"]["minimization"]
    assert retained["state"] == "REVIEW_REQUIRED" and retained["finished_at"] is not None
    assert result["stop_reason"] == "WORKER_LEASE_EXPIRED"
    assert result["usage"]["target_calls"] == 1
    assert result["best_mutation_id"] == original["id"]
    await execute_job(api.factory, api.settings, jid)
    assert (await api.client.post(f"/api/v1/jobs/{jid}/resume")).status_code == 409
    assert (await api.client.get(f"/api/v1/jobs/{jid}")).json() == retained


@pytest.mark.parametrize("mode", ["DISABLED", "LIVE"])
async def test_unavailable_provider_does_not_consume_refinement_capacity(
    api, vtc_payload, monkeypatch, mode
):
    from pydantic import SecretStr

    fid, original = await source(api, vtc_payload, monkeypatch)
    response = await api.client.post(
        f"/api/v1/findings/{fid}/minimize",
        json={"probe_limit": 1},
        headers={"Idempotency-Key": "mode-before-capacity"},
    )
    assert response.status_code == 202, response.text
    jid = UUID(response.json()["job_id"])
    changed = api.settings.model_copy(
        update={
            "provider_mode": mode,
            "run_meta_live_tests": mode == "LIVE",
            "model_api_key": SecretStr("test-only-never-dispatched"),
        }
    )
    await execute_job(api.factory, changed, jid)
    retained = (await api.client.get(f"/api/v1/jobs/{jid}")).json()
    assert retained["state"] == "REVIEW_REQUIRED" and retained["finished_at"] is not None
    assert retained["result"]["synthetic"] is True
    assert retained["result"]["minimization"]["stop_reason"] == "PROVIDER_MODE_UNAVAILABLE"
    with api.factory() as uow:
        assert uow.connection.scalar(select(uow.tables.experiments.c.candidates_created)) == 1
        assert not uow.connection.scalar(
            select(uow.tables.mutations.c.id).where(
                uow.tables.mutations.c.minimization_job_id == jid
            )
        )
        assert not uow.connection.scalar(
            select(uow.tables.usage_events.c.id).where(
                uow.tables.usage_events.c.background_job_id == jid
            )
        )
