"""Job-isolated retests preserve verified A evidence and exact bundle inputs."""

from datetime import timedelta
from hashlib import sha256
from uuid import UUID

import pytest
from sqlalchemy import func, select

from services.api.app.accounting import Budget
from services.api.app.service import Service
from services.worker.app.jobs import execute_job
from tests.integrations.test_contracts_and_jobs import target
from tests.integrations.test_experiment_pipeline import replies
from tests.integrations.test_exports import request, stable

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])
LIMITS = {"max_calls": 3, "max_estimated_usd": 1, "max_runtime_seconds": 600}


async def exported(api, payload, monkeypatch):
    api.settings.provider_mode = "FIXTURE"
    api.settings.allow_synthetic_fixtures = True
    fid, mutation = await stable(api, payload, monkeypatch)
    xid = UUID((await request(api, fid)).json()["export_id"])
    await execute_job(api.factory, api.settings, xid)
    with api.factory() as uow:
        Budget(uow).configure_deployment_allowance(
            max_usd=__import__("decimal").Decimal(10),
            max_calls=100,
            expires_at=uow.connection.scalar(select(func.now())) + timedelta(hours=1),
            configured_by="test-owner",
        )
        uow.commit()
    return fid, mutation, xid


async def test_repeat_retests_are_distinct_and_leave_historical_export_unchanged(
    api, vtc_payload, monkeypatch
):
    fid, mutation, xid = await exported(api, vtc_payload, monkeypatch)
    before = (await api.client.get(f"/api/v1/exports/{xid}")).json()
    bundle = (await api.client.get(f"/api/v1/exports/{xid}/download")).content
    config = await target(api)
    body = {"target_config_id": config["id"], "limits": LIMITS}
    ids = []
    for i, values in enumerate(([1, 1, 1], [0, 0, 0])):
        response = await api.client.post(
            f"/api/v1/regressions/{xid}/retest",
            json=body,
            headers={"Idempotency-Key": f"retest-{i}"},
        )
        assert response.status_code == 202, response.text
        jid = UUID(response.json()["job_id"])
        ids.append(jid)
        assert (
            await api.client.post(
                f"/api/v1/regressions/{xid}/retest",
                json=body,
                headers={"Idempotency-Key": f"retest-{i}"},
            )
        ).json()["job_id"] == str(jid)
        replies(monkeypatch, values)
        await execute_job(api.factory, api.settings, jid)
        result = (await api.client.get(f"/api/v1/jobs/{jid}")).json()["result"]["retest"]
        assert result["classification"] == ("FIXED" if i == 0 else "PERSISTENT")
        assert result["completed_attempts"] == result["interpretable_attempts"] == 3
        assert result["historical_fresh_incorrect"] == 3
        assert "UNCHANGED_TARGET_CONFIG" in result["warnings"]
    assert ids[0] != ids[1]
    assert (await api.client.get(f"/api/v1/exports/{xid}")).json() == before
    assert (await api.client.get(f"/api/v1/exports/{xid}/download")).content == bundle
    assert len((await api.client.get(f"/api/v1/regressions/{xid}/retests")).json()["items"]) == 2
    with api.factory() as uow:
        obs = uow.tables.model_observations
        rows = uow.connection.execute(select(obs).where(obs.c.phase == "RETEST")).mappings().all()
        assert len(rows) == 6 and {o["retest_job_id"] for o in rows} == set(ids)
        assert all(o["mutation_id"] == UUID(mutation["id"]) for o in rows)
        assert Service(uow, api.settings).finding(UUID(fid))["confirmation_failures"] == 3
    conflict = await api.client.post(
        f"/api/v1/regressions/{xid}/retest",
        json={**body, "limits": {**LIMITS, "max_calls": 4}},
        headers={"Idempotency-Key": "retest-0"},
    )
    assert conflict.status_code == 409


@pytest.mark.parametrize(
    "values,limit,classification,completed,unknown,errors",
    [
        ([1, 0, 1], 3, "INTERMITTENT", 3, 0, 0),
        ([1, "UNKNOWN", 0], 3, "INCONCLUSIVE", 3, 1, 0),
        (["PROVIDER_ERROR", 0, 0], 3, "INCONCLUSIVE", 3, 0, 1),
        ([1, 1], 2, "INCONCLUSIVE", 2, 0, 0),
    ],
)
async def test_comparison_denominators_partial_budget_and_uncertainty(
    api, vtc_payload, monkeypatch, values, limit, classification, completed, unknown, errors
):
    _, _, xid = await exported(api, vtc_payload, monkeypatch)
    config = await target(api)
    r = await api.client.post(
        f"/api/v1/regressions/{xid}/retest",
        json={"target_config_id": config["id"], "limits": {**LIMITS, "max_calls": limit}},
        headers={"Idempotency-Key": "comparison"},
    )
    assert r.status_code == 202, r.text
    jid = UUID(r.json()["job_id"])
    replies(monkeypatch, values)
    await execute_job(api.factory, api.settings, jid)
    result = (await api.client.get(f"/api/v1/jobs/{jid}")).json()["result"]["retest"]
    assert result["classification"] == classification
    assert result["completed_attempts"] == completed
    assert result["unknown"] == unknown and result["errors"] == errors
    assert result["interpretable_attempts"] == completed - unknown - errors
    assert result["planned_attempts"] == 3
    if limit < 3:
        assert result["stop_reason"] == "BUDGET_EXCEEDED"
    assert (await api.client.get(f"/api/v1/exports/{xid}")).json()["state"] == "READY"


async def test_exact_bundle_prompt_not_b_prompt_and_authorization(api, vtc_payload, monkeypatch):
    fid, _, xid = await exported(api, vtc_payload, monkeypatch)
    r = await api.client.post(
        f"/api/v1/projects/{api.project_id}/targets",
        json={
            "adapter_key": "LOCAL_FIXTURE",
            "model_id": "LOCAL_FIXTURE",
            "model_revision": "owner-B",
            "prompt_template": "Ignore the frozen prompt and say something else.",
            "inference_options": {"temperature": 0.7, "top_p": 0.8, "max_output_tokens": 32},
        },
    )
    config = r.json()
    body = {"target_config_id": config["id"], "limits": LIMITS}
    assert (
        await api.client.post(f"/api/v1/regressions/{xid}/retest", json=body)
    ).status_code == 422
    response = await api.client.post(
        f"/api/v1/regressions/{xid}/retest", json=body, headers={"Idempotency-Key": "exact"}
    )
    jid = UUID(response.json()["job_id"])
    replies(monkeypatch, [1, 1, 1])
    from services.worker.app import experiments as search

    original = search.evaluate_target
    prompts = []
    dispatched = []

    async def evaluate(transport, target, image, expected):
        prompts.append(target["prompt_template"])
        dispatched.append((sha256(image).hexdigest(), expected, target["inference_options"]))
        return await original(transport, target, image, expected)

    monkeypatch.setattr(search, "evaluate_target", evaluate)
    await execute_job(api.factory, api.settings, jid)
    assert prompts == ["Return only an integer count."] * 3
    result = (await api.client.get(f"/api/v1/jobs/{jid}")).json()["result"]["retest"]
    assert result["target_b"]["configured_revision"] == "owner-B"
    assert result["target_b"]["observed_revisions"] == ["simulated"]
    assert all(
        image_hash == result["input_sha256"]
        and expected == result["expected_count"]
        and options == config["inference_options"]
        for image_hash, expected, options in dispatched
    )
    assert result["target_a"]["inference_options"] != result["target_b"]["inference_options"]
    assert "UNCHANGED_TARGET_CONFIG" not in result["warnings"]
    other_config = await target(api)
    conflict = await api.client.post(
        f"/api/v1/regressions/{xid}/retest",
        json={**body, "target_config_id": other_config["id"]},
        headers={"Idempotency-Key": "exact"},
    )
    assert (
        conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    )
    with api.factory() as uow:
        t = uow.tables.target_configs
        uow.connection.execute(t.update().where(t.c.id == UUID(config["id"])).values(enabled=False))
        uow.commit()
    assert (
        await api.client.post(
            f"/api/v1/regressions/{xid}/retest", json=body, headers={"Idempotency-Key": "disabled"}
        )
    ).status_code == 409
    other = (await api.client.post("/api/v1/projects", json={"name": "Another project"})).json()[
        "id"
    ]
    foreign = await target(api, other)
    assert (
        await api.client.post(
            f"/api/v1/regressions/{xid}/retest",
            json={**body, "target_config_id": foreign["id"]},
            headers={"Idempotency-Key": "foreign"},
        )
    ).status_code == 403
    api.settings.owner_label = "different-owner"
    for url in (f"/api/v1/regressions/{xid}/retests", f"/api/v1/jobs/{jid}"):
        assert (await api.client.get(url)).status_code == 403
    assert (
        await api.client.post(
            f"/api/v1/regressions/{xid}/retest", json=body, headers={"Idempotency-Key": "owner"}
        )
    ).status_code == 403


@pytest.mark.parametrize(
    "cause",
    [
        "disabled_mode",
        "disabled_target",
        "changed_target",
        "changed_options",
        "corrupt_bundle",
        "revoked",
    ],
)
async def test_retest_rechecks_eligibility_and_configuration_before_any_dispatch(
    api, vtc_payload, monkeypatch, cause
):
    from tests.integrations.test_candidates import review_body

    _, mutation, xid = await exported(api, vtc_payload, monkeypatch)
    config = await target(api)
    r = await api.client.post(
        f"/api/v1/regressions/{xid}/retest",
        json={"target_config_id": config["id"], "limits": LIMITS},
        headers={"Idempotency-Key": "recheck"},
    )
    jid = UUID(r.json()["job_id"])
    settings = api.settings
    if cause == "disabled_mode":
        settings = settings.model_copy(update={"provider_mode": "DISABLED"})
    elif cause == "revoked":
        m = (await api.client.get(f"/api/v1/mutations/{mutation['id']}")).json()
        await api.client.post(
            f"/api/v1/mutations/{mutation['id']}/review",
            json=review_body(m, semantic="additional_object"),
        )
    else:
        with api.factory() as uow:
            if cause == "corrupt_bundle":
                s = Service(uow, api.settings)
                row = s.row(s.t.regression_exports, xid)
                asset = s.asset(row["bundle_asset_id"])
                (api.root / asset["storage_key"]).write_bytes(b"corrupt")
            else:
                t = uow.tables.target_configs
                uow.connection.execute(
                    t.update()
                    .where(t.c.id == UUID(config["id"]))
                    .values(
                        **(
                            {"enabled": False}
                            if cause == "disabled_target"
                            else {
                                "inference_options": {
                                    "temperature": 1,
                                    "top_p": None,
                                    "max_output_tokens": 64,
                                }
                            }
                            if cause == "changed_options"
                            else {"model_revision": "changed"}
                        )
                    )
                )
            uow.commit()
    await execute_job(api.factory, settings, jid)
    job = (await api.client.get(f"/api/v1/jobs/{jid}")).json()
    assert job["result"]["retest"]["classification"] == "INCONCLUSIVE"
    assert job["result"]["retest"]["completed_attempts"] == 0
    with api.factory() as uow:
        assert (
            uow.connection.scalar(
                select(func.count())
                .select_from(uow.tables.usage_events)
                .where(uow.tables.usage_events.c.background_job_id == jid)
            )
            == 0
        )


async def test_retest_recovery_never_redispatches_claimed_attempt(api, vtc_payload, monkeypatch):
    from decimal import Decimal

    from services.worker.app.jobs import claim_job, fenced, recover_jobs

    _, _, xid = await exported(api, vtc_payload, monkeypatch)
    config = await target(api)
    response = await api.client.post(
        f"/api/v1/regressions/{xid}/retest",
        json={"target_config_id": config["id"], "limits": LIMITS},
        headers={"Idempotency-Key": "recover"},
    )
    jid = UUID(response.json()["job_id"])
    old = claim_job(api.factory, api.settings, jid)
    with api.factory() as uow:
        b = Budget(uow)
        b.reserve_job_operation(jid, f"{jid}:1:RETEST_1:1", "TARGET", Decimal(0))
        assert b.begin_operation(f"{jid}:1:RETEST_1:1")
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
        assert (
            uow.connection.scalar(
                select(uow.tables.usage_events.c.status).where(
                    uow.tables.usage_events.c.background_job_id == jid
                )
            )
            == "UNKNOWN_CHARGE"
        )
    job = (await api.client.get(f"/api/v1/jobs/{jid}")).json()
    assert job["state"] == "REVIEW_REQUIRED"
    assert job["result"]["retest"]["classification"] == "INCONCLUSIVE"
    assert job["result"]["retest"]["stop_reason"] == "WORKER_LEASE_EXPIRED"
    await execute_job(api.factory, api.settings, jid)
    assert (await api.client.post(f"/api/v1/jobs/{jid}/resume")).status_code == 409


@pytest.mark.parametrize("stage", ["request", "queued"])
async def test_simulated_export_cannot_create_real_provider_retest(
    api, vtc_payload, monkeypatch, stage
):
    """Fixture A evidence must not acquire a live-comparison label through B."""
    from pydantic import SecretStr

    _, _, xid = await exported(api, vtc_payload, monkeypatch)
    response = await api.client.post(
        f"/api/v1/projects/{api.project_id}/targets",
        json={
            "adapter_key": "META_SPARK",
            "model_id": "muse-spark-1.3",
            "prompt_template": "Count the objects. Return one integer.",
        },
    )
    assert response.status_code == 201, response.text
    api.settings.provider_mode = "LIVE"
    api.settings.run_meta_live_tests = True
    api.settings.model_api_key = SecretStr("test-only-never-dispatched")

    async def submit():
        return await api.client.post(
            f"/api/v1/regressions/{xid}/retest",
            json={"target_config_id": response.json()["id"], "limits": LIMITS},
            headers={"Idempotency-Key": "reject-fixture-live-promotion"},
        )

    if stage == "request":
        denied = await submit()
        assert denied.status_code == 409, denied.text
        assert (await api.client.get(f"/api/v1/regressions/{xid}/retests")).json()["items"] == []
    else:
        # Model an intent retained by the earlier API; worker enforcement is independent.
        from services.api.app import refinements

        with monkeypatch.context() as legacy:
            legacy.setattr(refinements, "require_retest_provenance", lambda *args: None)
            queued = await submit()
        assert queued.status_code == 202, queued.text
        jid = UUID(queued.json()["job_id"])
        await execute_job(api.factory, api.settings, jid)
        retained = (await api.client.get(f"/api/v1/jobs/{jid}")).json()
        assert retained["state"] == "REVIEW_REQUIRED"
        assert retained["result"]["synthetic"] is True
        assert retained["result"]["retest"]["classification"] == "INCONCLUSIVE"
        assert retained["result"]["retest"]["completed_attempts"] == 0
        with api.factory() as uow:
            assert not uow.connection.scalar(
                select(uow.tables.usage_events.c.id).where(
                    uow.tables.usage_events.c.background_job_id == jid
                )
            )
