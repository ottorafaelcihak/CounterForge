"""Exports bind current sealed validity to retained, accounted fresh observations."""

import io
import json
from datetime import timedelta
from uuid import UUID
from zipfile import ZipFile

import pytest
from sqlalchemy import func, select

from services.api.app.service import Service
from services.worker.app.experiments import execute_experiment
from services.worker.app.jobs import execute_job, recover_jobs
from tests.integrations.test_experiment_pipeline import fixture_settings, replies, valid_candidate

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


async def stable(api, vtc_payload, monkeypatch):
    api.settings.provider_mode = "FIXTURE"
    api.settings.allow_synthetic_fixtures = True
    eid, mutation = await valid_candidate(api, vtc_payload)
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.experiments.update()
            .where(uow.tables.experiments.c.id == UUID(eid))
            .values(state="BASELINE_VALIDATING")
        )
        uow.commit()
    replies(monkeypatch, [1, 1, 1])
    for _ in range(3):
        await execute_experiment(api.factory, fixture_settings(api), UUID(eid))
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.experiments.update()
            .where(uow.tables.experiments.c.id == UUID(eid))
            .values(state="EVALUATING")
        )
        uow.commit()
    replies(monkeypatch, [0, 0, 0, 0])
    for _ in range(4):
        await execute_experiment(api.factory, fixture_settings(api), UUID(eid))
    with api.factory() as uow:
        finding = uow.connection.execute(select(uow.tables.findings)).mappings().one()
        return str(finding["id"]), mutation


async def request(api, fid, key="export-once"):
    return await api.client.post(
        f"/api/v1/findings/{fid}/exports", headers={"Idempotency-Key": key}
    )


async def test_export_idempotent_cpu_worker_safe_bundle_and_revocation(
    api, vtc_payload, monkeypatch
):
    from tests.integrations.test_candidates import review_body

    fid, mutation = await stable(api, vtc_payload, monkeypatch)
    a = await request(api, fid)
    assert a.status_code == 202, a.text
    b = await request(api, fid)
    assert a.json() == b.json()
    xid = a.json()["export_id"]
    # Export is CPU only and must work even when providers are now disabled.
    await execute_job(
        api.factory, api.settings.model_copy(update={"provider_mode": "DISABLED"}), UUID(xid)
    )
    ready = await api.client.get(f"/api/v1/exports/{xid}")
    assert ready.json()["state"] == "READY", ready.text
    assert ready.json()["provenance"] == "LOCAL_FIXTURE"
    assert ready.json()["bundle_sha256"]
    download = await api.client.get(ready.json()["links"]["download"])
    assert download.status_code == 200
    with ZipFile(io.BytesIO(download.content)) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["provenance"] == "LOCAL_FIXTURE"
        assert manifest["confirmation"]["fresh_failures"] == 3
        assert bundle.read("evidence/failing.png")
    with api.factory() as uow:
        exported = Service(uow, api.settings).row(uow.tables.regression_exports, UUID(xid))
        bundle_id, manifest_id = exported["bundle_asset_id"], exported["manifest_asset_id"]
        assert not Service(uow, api.settings).finding(UUID(fid))["demo_approved_at"]
    detail = await api.client.get(f"/api/v1/findings/{fid}/evidence")
    assert detail.status_code == 200, detail.text
    ev = detail.json()
    assert ev["provenance"] == "LOCAL_FIXTURE" and len(ev["observations"]) == 7
    assert ev["assets"]["diff"]["content_url"].startswith("/api/v1/assets/")
    assert len([o for o in ev["observations"] if o["phase"] == "CONFIRMATION"]) == 3
    assert all(
        o["raw_text"] == "0" and o["parsed_count"] == 0
        for o in ev["observations"]
        if o["phase"] != "SEED_BASELINE"
    )
    assert all(term not in detail.text for term in ("storage_key", "api_key", "private_url"))
    assert (await api.client.get("/api/v1/regressions")).json()["items"][0]["state"] == "READY"
    current = (await api.client.get(f"/api/v1/mutations/{mutation['id']}")).json()
    assert (
        await api.client.post(
            f"/api/v1/mutations/{mutation['id']}/review",
            json=review_body(current, semantic="additional_object"),
        )
    ).status_code == 200
    assert (await api.client.get(f"/api/v1/exports/{xid}")).json()["state"] == "REVOKED"
    assert (await api.client.get(f"/api/v1/exports/{xid}/download")).status_code == 409
    for aid in (bundle_id, manifest_id):
        assert (await api.client.get(f"/api/v1/assets/{aid}/content")).status_code == 409
    assert (await api.client.get("/api/v1/regressions")).json()["items"][0]["state"] == "REVOKED"
    assert (await request(api, fid, "again")).status_code == 409
    # Immutable frozen blob remains retained even though eligibility was revoked.
    with api.factory() as uow:
        assert Service(uow, api.settings).artifacts.read_verified(bundle_id) == download.content


async def test_export_rejects_partial_confirmation_and_missing_raw_evidence(
    api, vtc_payload, monkeypatch
):
    fid, _ = await stable(api, vtc_payload, monkeypatch)
    with api.factory() as uow:
        f = uow.tables.findings
        uow.connection.execute(
            f.update()
            .where(f.c.id == UUID(fid))
            .values(confirmation_attempts=2, confirmation_failures=2)
        )
        uow.commit()
    assert (await request(api, fid)).status_code == 409
    with api.factory() as uow:
        f = uow.tables.findings
        uow.connection.execute(
            f.update()
            .where(f.c.id == UUID(fid))
            .values(confirmation_attempts=3, confirmation_failures=3)
        )
        raw = uow.connection.scalar(
            select(uow.tables.model_observations.c.raw_output_asset_id).limit(1)
        )
        asset = Service(uow, api.settings).asset(raw)
        path = api.root / asset["storage_key"]
        path.write_bytes(b"corrupt")
        uow.commit()
    assert (await request(api, fid)).status_code == 409


async def test_export_cpu_recovery_and_stale_fence(api, vtc_payload, monkeypatch):
    from services.worker.app.jobs import claim_job

    fid, _ = await stable(api, vtc_payload, monkeypatch)
    xid = UUID((await request(api, fid)).json()["export_id"])
    old = claim_job(api.factory, api.settings, xid)
    assert old is not None
    with api.factory() as uow:
        j = uow.tables.background_jobs
        uow.connection.execute(
            j.update()
            .where(j.c.id == xid)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        uow.commit()
    assert xid in recover_jobs(api.factory, api.settings)
    await execute_job(api.factory, api.settings, xid)
    assert (await api.client.get(f"/api/v1/exports/{xid}")).json()["state"] == "READY"
    from services.worker.app.jobs import fenced

    with api.factory() as uow:
        assert fenced(uow, old) is None


async def test_orphan_succeeded_job_detail_and_library_agree_failed(api, vtc_payload, monkeypatch):
    fid, _ = await stable(api, vtc_payload, monkeypatch)
    xid = UUID((await request(api, fid)).json()["export_id"])
    with api.factory() as uow:
        j = uow.tables.background_jobs
        uow.connection.execute(j.update().where(j.c.id == xid).values(state="SUCCEEDED"))
        uow.commit()
    detail = await api.client.get(f"/api/v1/exports/{xid}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["state"] == "FAILED"
    assert (await api.client.get("/api/v1/regressions")).json()["items"] == [detail.json()]


async def test_revocation_during_cpu_build_never_registers_ready(api, vtc_payload, monkeypatch):
    from counterforge_contracts.requests import MutationReviewRequest

    from services.worker.app import export_jobs
    from tests.integrations.test_candidates import review_body

    fid, mutation = await stable(api, vtc_payload, monkeypatch)
    xid = UUID((await request(api, fid)).json()["export_id"])
    real_build = export_jobs.build_bundle

    def revoke_then_build(manifest, evidence):
        with api.factory() as uow:
            s = Service(uow, api.settings)
            s.candidates.review(
                UUID(mutation["id"]),
                MutationReviewRequest.model_validate(
                    review_body(mutation, semantic="additional_object")
                ),
            )
            uow.commit()
        return real_build(manifest, evidence)

    monkeypatch.setattr(export_jobs, "build_bundle", revoke_then_build)
    await execute_job(api.factory, api.settings, xid)
    detail = (await api.client.get(f"/api/v1/exports/{xid}")).json()
    assert detail["state"] == "FAILED" and detail["links"]["download"] is None
    with api.factory() as uow:
        assert not uow.connection.execute(select(uow.tables.regression_exports)).all()
        assert not uow.connection.execute(
            select(uow.tables.usage_events).where(
                uow.tables.usage_events.c.background_job_id == xid
            )
        ).all()


async def test_export_running_retry_cross_finding_key_conflict_and_owner_guards(
    api, vtc_payload, monkeypatch
):
    from services.worker.app.jobs import claim_job
    from tests.integrations.test_candidates import BRIGHT, create, review_body

    fid, mutation = await stable(api, vtc_payload, monkeypatch)
    first = await request(api, fid)
    xid = UUID(first.json()["export_id"])
    claim = claim_job(api.factory, api.settings, xid)
    assert claim
    retry = await request(api, fid)
    assert retry.json()["export_id"] == str(xid) and retry.json()["state"] == "RUNNING"
    with api.factory() as uow:
        f = Service(uow, api.settings).finding(UUID(fid))
        eid = f["experiment_id"]
        uow.connection.execute(
            uow.tables.experiments.update()
            .where(uow.tables.experiments.c.id == eid)
            .values(state="EVALUATING")
        )
        uow.commit()
    second = await create(api, str(eid), key="second", plan={**BRIGHT, "strength": 0.5})
    assert second.status_code == 201, second.text
    assert (
        await api.client.post(
            f"/api/v1/mutations/{second.json()['id']}/review", json=review_body(second.json())
        )
    ).status_code == 200
    replies(monkeypatch, [0, 0, 0, 0])
    for _ in range(4):
        await execute_experiment(api.factory, fixture_settings(api), eid)
    with api.factory() as uow:
        second_id = uow.connection.scalar(
            select(uow.tables.findings.c.id).where(uow.tables.findings.c.id != UUID(fid))
        )
        assert second_id
    conflict = await request(api, str(second_id))
    assert (
        conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    )
    for url in (
        f"/api/v1/exports/{xid}",
        f"/api/v1/exports/{xid}/download",
        f"/api/v1/findings/{fid}/evidence",
    ):
        assert (await api.client.get(url, headers={"Authorization": ""})).status_code == 401
    api.settings.owner_label = "another-owner"
    assert (await api.client.get(f"/api/v1/exports/{xid}")).status_code == 403
    assert (await api.client.get(f"/api/v1/findings/{fid}/evidence")).status_code == 403


async def test_finding_usage_reports_calls_and_unknown_tokens_honestly(
    api, vtc_payload, monkeypatch
):
    fid, _ = await stable(api, vtc_payload, monkeypatch)
    response = await api.client.get(f"/api/v1/findings/{fid}/evidence")
    assert response.status_code == 200, response.text
    usage = response.json()["usage"]
    assert usage["target_calls"] == 7 and usage["calls"] == 7
    assert usage["known_tokens_in"] == usage["known_tokens_out"] == 0
    assert usage["unknown_token_calls"] == 7
    assert usage["actual_usd"] == 0


async def test_damaged_observation_does_not_break_finding_or_regression_lists(
    api, vtc_payload, monkeypatch
):
    fid, _ = await stable(api, vtc_payload, monkeypatch)
    xid = UUID((await request(api, fid)).json()["export_id"])
    await execute_job(api.factory, api.settings, xid)
    with api.factory() as uow:
        s = Service(uow, api.settings)
        e = s.finding(UUID(fid))["experiment_id"]
        raw = s.db.scalar(select(s.t.model_observations.c.raw_output_asset_id).limit(1))
        asset = s.asset(raw)
        (api.root / asset["storage_key"]).write_bytes(b"damaged")
    listed = await api.client.get(f"/api/v1/experiments/{e}/findings")
    assert listed.status_code == 200 and listed.json()["items"][0]["evidence"] is None
    detail = await api.client.get(f"/api/v1/findings/{fid}")
    assert detail.status_code == 200 and detail.json()["evidence"] is None
    # List uses immutable registration/current DB validity; download rechecks bytes.
    library = await api.client.get("/api/v1/regressions")
    assert library.status_code == 200 and library.json()["items"][0]["export_id"] == str(xid)
    assert library.json()["items"][0]["links"]["download"] is None
    assert (await api.client.get(f"/api/v1/exports/{xid}/download")).status_code == 409
