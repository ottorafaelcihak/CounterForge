"""Protocol scheduler tests without providers; DB adapter cases use existing API fixture."""

from uuid import UUID

import pytest

from evaluations.protocol import Protocol, freeze_protocol
from evaluations.runner import EvaluationRun


def protocol():
    return freeze_protocol(
        dict(
            revision="test",
            dataset_sha256="a" * 64,
            policy_revision="strict-binary-human-gates-1",
            target_revision="b" * 64,
            code_revision="0a3ab3d",
            random_seed=71,
            target_calls_per_arm=8,
            max_candidates_per_arm=3,
            max_api_calls=300,
            max_usd="1.00",
            max_runtime_seconds=1200,
        )
    )


class Adapter:
    def __init__(self, failure=None):
        self.log = []
        self.failure = failure

    def create(self, key, case, arm, protocol):
        return str(UUID(int=1 + len(key))) + key

    async def tick(self, eid, phase):
        self.log.append((eid, phase))
        return (
            "EXCLUDED"
            if phase == "BASELINE" and self.failure and self.failure in eid
            else "READY"
            if phase == "BASELINE"
            else "COMPLETED"
        )


async def test_all_arm_baselines_before_search_durable_immutable_resume(tmp_path):
    p = protocol()
    adapter = Adapter()
    r = EvaluationRun(tmp_path, p, [{"case_id": "c", "split": "development"}], adapter)
    await r.run()
    assert [phase for _, phase in adapter.log] == ["BASELINE"] * 3 + ["SEARCH"] * 3
    state = r.state_path.read_bytes()
    await r.run()
    assert state == r.state_path.read_bytes() and len(adapter.log) == 6
    changed = p.model_dump(mode="json")
    changed["random_seed"] += 1
    with pytest.raises(ValueError, match="integrity|immutable"):
        EvaluationRun(tmp_path, Protocol.model_validate(changed), [], adapter)


async def test_baseline_failure_excludes_all_arms_before_search(tmp_path):
    a = Adapter(failure="spark_det")
    r = EvaluationRun(tmp_path, protocol(), [{"case_id": "c", "split": "development"}], a)
    await r.run()
    assert all(phase == "BASELINE" for _, phase in a.log)
    assert r.state["seeds"][0]["status"] == "EXCLUDED"


async def test_interruption_saved_before_dispatch_and_no_blind_replay(tmp_path):
    class Interrupted(Adapter):
        async def tick(self, eid, phase):
            raise KeyboardInterrupt()

    r = EvaluationRun(
        tmp_path, protocol(), [{"case_id": "c", "split": "development"}], Interrupted()
    )
    with pytest.raises(KeyboardInterrupt):
        await r.run()
    assert r.state["status"] == "INTERRUPTED"
    assert all(arm["experiment_id"] for arm in r.state["seeds"][0]["arms"])


async def test_existing_service_adapter_barrier_and_global_call_cap(api, vtc_payload, monkeypatch):
    from evaluations.adapter import ExistingExperimentAdapter
    from services.api.app.service import Service, fingerprint
    from services.worker.app import experiments as worker
    from tests.integrations.test_contracts_and_jobs import contract, target
    from tests.integrations.test_experiment_pipeline import replies

    api.settings.provider_mode = "FIXTURE"
    api.settings.allow_synthetic_fixtures = True
    vtc_payload["allowed_mutations"] = ["brightness_shift"]
    seed, _, vtc = await contract(api, vtc_payload)
    vtc_payload["allowed_mutations"] = [
        "brightness_shift",
        "background_replacement",
        "background_clutter",
    ]
    full = await api.client.post(f"/api/v1/seeds/{seed['id']}/contracts", json=vtc_payload)
    assert full.status_code == 201, full.text
    t = await target(api)
    with api.factory() as uow:
        revision = fingerprint(
            {
                key: Service(uow, api.settings).target(UUID(t["id"]))[key]
                for key in (
                    "adapter_key",
                    "provider",
                    "model_id",
                    "model_revision",
                    "prompt_template",
                    "inference_options",
                )
            }
        )
    data = protocol().model_dump(mode="json")
    data.pop("sha256")
    data.update(target_revision=revision, max_api_calls=4)
    p = freeze_protocol(data)
    adapter = ExistingExperimentAdapter(
        api.factory,
        api.settings,
        UUID(api.project_id),
        {
            "c": {
                "contract_id": vtc["id"],
                "full_contract_id": full.json()["id"],
                "target_config_id": t["id"],
            }
        },
        p,
    )
    from datetime import UTC, datetime

    adapter.prepare(
        [
            dict(key="test:" + arm, case={"case_id": "c", "split": "development"}, arm=arm)
            for arm in p.arms
        ],
        datetime.now(UTC).isoformat(),
    )
    ids = [
        adapter.create("test:" + arm, {"case_id": "c", "split": "development"}, arm, p)
        for arm in p.arms
    ]
    adapter.bind(ids, [[*ids]])
    replies(monkeypatch, [1] * 12)
    for _ in range(3):
        await worker.execute_experiment(api.factory, api.settings, UUID(ids[0]))
    await worker.execute_experiment(api.factory, api.settings, UUID(ids[0]))
    with api.factory() as uow:
        s = Service(uow, api.settings)
        assert s.experiment(UUID(ids[0]))["state"] == "READY"
        assert not list(uow.connection.execute(s.t.mutation_plans.select()))
    for _ in range(3):
        await worker.execute_experiment(api.factory, api.settings, UUID(ids[1]))
    with api.factory() as uow:
        from sqlalchemy import func, select

        s = Service(uow, api.settings)
        assert (
            uow.connection.scalar(
                select(func.count())
                .select_from(s.t.usage_events)
                .where(s.t.usage_events.c.dispatched_at.is_not(None))
            )
            == 4
        )


async def test_service_worker_pause_api_review_resume_preserves_ids_baselines_costs(
    api, vtc_payload, monkeypatch
):
    """Real Service/worker: human pause -> API candidate review -> resume.

    Experiment IDs, baseline observations and consumed target calls must be
    preserved across the pause; resume must not re-spend baselines.
    """
    from evaluations.adapter import ExistingExperimentAdapter
    from services.api.app.service import Service, fingerprint
    from services.worker.app import experiments as worker
    from tests.integrations.test_candidates import BRIGHT, create, review_body
    from tests.integrations.test_contracts_and_jobs import contract, target
    from tests.integrations.test_experiment_pipeline import replies

    api.settings.provider_mode = "FIXTURE"
    api.settings.allow_synthetic_fixtures = True
    settings = api.settings.model_copy(
        update={"provider_mode": "FIXTURE", "allow_synthetic_fixtures": True}
    )
    vtc_payload["allowed_mutations"] = ["brightness_shift"]
    seed, _, vtc = await contract(api, vtc_payload)
    vtc_payload["allowed_mutations"] = [
        "brightness_shift",
        "background_replacement",
        "background_clutter",
    ]
    full = await api.client.post(f"/api/v1/seeds/{seed['id']}/contracts", json=vtc_payload)
    assert full.status_code == 201, full.text
    t = await target(api)
    with api.factory() as uow:
        revision = fingerprint(
            {
                key: Service(uow, api.settings).target(UUID(t["id"]))[key]
                for key in (
                    "adapter_key",
                    "provider",
                    "model_id",
                    "model_revision",
                    "prompt_template",
                    "inference_options",
                )
            }
        )
    data = protocol().model_dump(mode="json")
    data.pop("sha256")
    data.update(target_revision=revision, target_calls_per_arm=20, max_api_calls=100)
    p = freeze_protocol(data)
    adapter = ExistingExperimentAdapter(
        api.factory,
        api.settings,
        UUID(api.project_id),
        {
            "c": {
                "contract_id": vtc["id"],
                "full_contract_id": full.json()["id"],
                "target_config_id": t["id"],
            }
        },
        p,
    )
    from datetime import UTC, datetime

    adapter.prepare(
        [
            dict(key="pause:" + arm, case={"case_id": "c", "split": "development"}, arm=arm)
            for arm in p.arms
        ],
        datetime.now(UTC).isoformat(),
    )
    ids = [
        adapter.create("pause:" + arm, {"case_id": "c", "split": "development"}, arm, p)
        for arm in p.arms
    ]
    adapter.bind(ids, [[*ids]])
    # Baselines: 3 CORRECT per arm (expected count is 1 in this fixture case).
    replies(monkeypatch, [1] * 9)
    for eid in ids:
        for _ in range(3):
            await worker.execute_experiment(api.factory, settings, UUID(eid))
    with api.factory() as uow:
        s = Service(uow, api.settings)
        before = {eid: dict(s.experiment(UUID(eid))) for eid in ids}
        assert all(e["state"] == "READY" and e["calls_consumed"] == 3 for e in before.values())
    eid = ids[0]
    # Human candidate review through the public API while the arm is live.
    mutation = (await create(api, eid, plan=BRIGHT)).json()
    reviewed = await api.client.post(
        f"/api/v1/mutations/{mutation['id']}/review", json=review_body(mutation)
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["validation"]["verdict"] == "VALID"
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.experiments.update()
            .where(uow.tables.experiments.c.id == UUID(eid))
            .values(state="EVALUATING")
        )
        uow.commit()
    paused = await api.client.post(f"/api/v1/experiments/{eid}/pause")
    assert paused.json()["state"] == "PAUSED", paused.text
    resumed = await api.client.post(f"/api/v1/experiments/{eid}/resume")
    assert resumed.status_code == 202, resumed.text
    with api.factory() as uow:
        s = Service(uow, api.settings)
        after = dict(s.experiment(UUID(eid)))
        assert str(after["id"]) == eid
        assert after["calls_consumed"] == 3  # baselines not re-spent
        baselines = list(
            uow.connection.execute(
                s.t.model_observations.select().where(
                    s.t.model_observations.c.experiment_id == UUID(eid),
                    s.t.model_observations.c.phase == "SEED_BASELINE",
                )
            ).mappings()
        )
        assert len(baselines) == 3 and all(o["outcome"] == "CORRECT" for o in baselines)
    # Screening + 3 fresh confirmations complete on the same experiment.
    replies(monkeypatch, [0, 0, 0, 0])
    for _ in range(4):
        await worker.execute_experiment(api.factory, settings, UUID(eid))
    with api.factory() as uow:
        s = Service(uow, api.settings)
        done = dict(s.experiment(UUID(eid)))
        assert done["calls_consumed"] == 7  # 3 baseline + 1 screen + 3 confirm
        findings = list(uow.connection.execute(s.t.findings.select()).mappings())
        assert findings and findings[0]["status"] == "STABLE"


async def test_live_authorization_replay_does_not_regain_paid_envelope(api, vtc_payload):
    """Two runs sharing one LIVE authorization share one aggregate envelope."""
    from decimal import Decimal
    from uuid import uuid4

    from counterforge_contracts.requests import StartExperiment
    from counterforge_contracts.vtc import Limits

    from services.api.app.accounting import Budget
    from services.api.app.errors import ServiceError
    from services.api.app.service import Service, fingerprint
    from tests.integrations.test_contracts_and_jobs import contract, target

    api.settings.provider_mode = "FIXTURE"
    api.settings.allow_synthetic_fixtures = True
    vtc_payload["allowed_mutations"] = ["brightness_shift"]
    seed, _, vtc = await contract(api, vtc_payload)
    vtc_payload["allowed_mutations"] = [
        "brightness_shift",
        "background_replacement",
        "background_clutter",
    ]
    full = await api.client.post(f"/api/v1/seeds/{seed['id']}/contracts", json=vtc_payload)
    assert full.status_code == 201, full.text
    t = await target(api)
    auth_sha = "a" * 64
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)

    def make_run(tag: str) -> list[str]:
        ids = [str(uuid4()) for _ in range(3)]
        declaration: dict = dict(
            protocol_sha256="b" * 64,
            dataset_sha256="c" * 64,
            policy_revision="strict-binary-human-gates-1",
            target_revision="d" * 64,
            experiment_ids=ids,
            baseline_groups=[ids],
            started_at=now.isoformat(),
            max_api_calls=2,
            max_usd="10",
            max_runtime_seconds=1200,
            authorization_expires_at=(now + timedelta(hours=1)).isoformat(),
            authorization_sha256=auth_sha,
        )
        declaration["run_sha256"] = fingerprint(declaration)
        declaration["sha256"] = fingerprint(declaration)
        created = []
        with api.factory() as uow:
            s = Service(uow, api.settings)
            for arm, eid in zip(("random_det", "spark_det", "full_system"), ids, strict=True):
                marker = dict(
                    protocol_sha256="b" * 64,
                    case_id="c",
                    split="development",
                    strategy=arm,
                    declared_policy="ONE_SHOT",
                    search_policy="ONE_SHOT",
                )
                ref = s.start_experiment(
                    StartExperiment(
                        project_id=UUID(api.project_id),
                        contract_id=UUID(full.json()["id"] if arm == "full_system" else vtc["id"]),
                        target_config_id=UUID(t["id"]),
                        mode="RANDOM_BASELINE" if arm == "random_det" else "ADAPTIVE",
                        search_policy="ONE_SHOT",
                        random_seed=71,
                        limits=Limits(
                            max_candidates=3,
                            max_target_calls=20,
                            max_estimated_usd=10.0,
                            max_runtime_seconds=1200,
                        ),
                    ),
                    f"{tag}:{arm}",
                    _evaluation={"evaluation": declaration, "evaluation_identity": marker},
                    _experiment_id=UUID(eid),
                )
                created.append(str(ref.experiment_id))
            uow.commit()
        return created

    run_a = make_run("auth-run-a")
    run_b = make_run("auth-run-b")
    assert run_a != run_b
    with api.factory() as uow:
        Budget(uow).reserve_operation(
            UUID(run_a[0]),
            "auth-run-a:op1",
            "TARGET",
            Decimal(0),
            target_calls=2,
            provider="TEST",
            model_id="LOCAL_FIXTURE",
        )
        uow.commit()
    # Original usage stays visible and counts against the replayed run.
    with api.factory() as uow:
        with pytest.raises(ServiceError, match="authorization exhausted"):
            Budget(uow).reserve_operation(
                UUID(run_b[0]),
                "auth-run-b:op1",
                "TARGET",
                Decimal(0),
                target_calls=1,
                provider="TEST",
                model_id="LOCAL_FIXTURE",
            )


async def test_random_loop_matches_adaptive_caps_without_planner(api, vtc_payload, monkeypatch):
    """RANDOM_V1: >=2 seeded slots, zero PLANNER usage, no Spark batches."""
    from evaluations.adapter import ExistingExperimentAdapter
    from services.api.app.service import Service, fingerprint
    from services.worker.app import experiments as worker
    from services.worker.app.random_strategy import random_plan
    from tests.integrations.test_candidates import fixture_scene, review_body
    from tests.integrations.test_contracts_and_jobs import target
    from tests.integrations.test_experiment_pipeline import replies

    api.settings.provider_mode = "FIXTURE"
    api.settings.allow_synthetic_fixtures = True
    # Real development-fixture seed (480x240, five approved masks) so the
    # fixture SAM guard accepts worker-driven candidate preparation.
    original_eid, _, settings, _, _ = await fixture_scene(api, vtc_payload)
    with api.factory() as uow:
        s0 = Service(uow, api.settings)
        fixture_contract_id = str(s0.experiment(UUID(original_eid))["contract_id"])
        seed_id = str(s0.contract(UUID(fixture_contract_id))["seed_id"])
    # Explicit deterministic and generator-ablation contracts, families in
    # declared protocol order (the adapter compares the tuples exactly).
    vtc_payload["allowed_mutations"] = ["brightness_shift"]
    bright = await api.client.post(f"/api/v1/seeds/{seed_id}/contracts", json=vtc_payload)
    assert bright.status_code == 201, bright.text
    vtc = bright.json()
    vtc_payload["allowed_mutations"] = [
        "brightness_shift",
        "background_replacement",
        "background_clutter",
    ]
    full_response = await api.client.post(f"/api/v1/seeds/{seed_id}/contracts", json=vtc_payload)
    assert full_response.status_code == 201, full_response.text
    full_contract_id = full_response.json()["id"]
    t = await target(api)
    with api.factory() as uow:
        revision = fingerprint(
            {
                key: Service(uow, api.settings).target(UUID(t["id"]))[key]
                for key in (
                    "adapter_key",
                    "provider",
                    "model_id",
                    "model_revision",
                    "prompt_template",
                    "inference_options",
                )
            }
        )
    data = protocol().model_dump(mode="json")
    data.pop("sha256")
    data.update(target_revision=revision, target_calls_per_arm=20, max_api_calls=100)
    p = freeze_protocol(data)
    assert p.primary_policy == "FEEDBACK_V1"
    adapter = ExistingExperimentAdapter(
        api.factory,
        api.settings,
        UUID(api.project_id),
        {
            "c": {
                "contract_id": vtc["id"],
                "full_contract_id": full_contract_id,
                "target_config_id": t["id"],
            }
        },
        p,
    )
    from datetime import UTC, datetime

    adapter.prepare(
        [
            dict(key="loop:" + arm, case={"case_id": "c", "split": "development"}, arm=arm)
            for arm in p.arms
        ],
        datetime.now(UTC).isoformat(),
    )
    ids = [
        adapter.create("loop:" + arm, {"case_id": "c", "split": "development"}, arm, p)
        for arm in p.arms
    ]
    adapter.bind(ids, [[*ids]])
    with api.factory() as uow:
        s = Service(uow, api.settings)
        snaps = {arm: dict(s.experiment(UUID(eid))) for arm, eid in zip(p.arms, ids, strict=True)}
        assert snaps["random_det"]["config_snapshot"]["search_policy"] == "RANDOM_V1"
        assert snaps["spark_det"]["config_snapshot"]["search_policy"] == "FEEDBACK_V1"
        assert snaps["random_det"]["config_snapshot"]["strategy"]["revision"] == "random-loop-v1"
        caps = {(e["max_candidates"], e["max_target_calls"]) for e in snaps.values()}
        assert len(caps) == 1  # shared attempt/target caps
    replies(monkeypatch, [5] * 60)
    for eid in ids:
        for _ in range(3):
            await worker.execute_experiment(api.factory, settings, UUID(eid))
    random_id = ids[0]

    async def forbidden_planner(*args, **kwargs):
        raise AssertionError("RANDOM_V1 must never call Spark")

    monkeypatch.setattr(worker.SparkPlanner, "plan", forbidden_planner)
    for _ in range(80):
        with api.factory() as uow:
            current = dict(Service(uow, api.settings).experiment(UUID(random_id)))
        state = current["state"]
        if state in {"COMPLETED", "BUDGET_EXHAUSTED"}:
            break
        if state == "PAUSED":
            # Only a candidate-review pause may be resumed here; any other
            # pause is a real blocker, not something to blindly retry.
            assert current["stop_reason"] == "CANDIDATE_HUMAN_REVIEW_REQUIRED", current
            mutations = (await api.client.get(f"/api/v1/experiments/{random_id}/mutations")).json()[
                "items"
            ]
            reviewed = 0
            for m in mutations:
                if m["validation"] and m["validation"]["verdict"] == "REVIEW_REQUIRED":
                    result = await api.client.post(
                        f"/api/v1/mutations/{m['id']}/review", json=review_body(m)
                    )
                    assert result.status_code == 200, result.text
                    reviewed += 1
            assert reviewed, f"review pause without a reviewable candidate: {current}"
            resumed = await api.client.post(f"/api/v1/experiments/{random_id}/resume")
            assert resumed.status_code == 202, resumed.text
        await worker.execute_experiment(api.factory, settings, UUID(random_id))
    else:
        raise AssertionError(f"random loop did not terminate: {current}")
    with api.factory() as uow:
        from sqlalchemy import select

        s = Service(uow, api.settings)
        done = dict(s.experiment(UUID(random_id)))
        assert done["state"] == "COMPLETED", done
        assert done["checkpoint_json"]["slot_index"] == done["max_candidates"] == 3
        plans = list(
            uow.connection.execute(
                select(s.t.mutation_plans)
                .where(s.t.mutation_plans.c.experiment_id == UUID(random_id))
                .order_by(s.t.mutation_plans.c.created_at)
            ).mappings()
        )
        assert len(plans) >= 2
        assert all(p_["strategy"] == "RANDOM" for p_ in plans)
        assert all(p_["strategy_revision"] == "random-loop-v1" for p_ in plans)
        assert all(p_["planner_model"] == "RANDOM_LOOP" for p_ in plans)
        assert all("batch" not in (p_["evidence_json"] or {}) for p_ in plans)
        assert all((p_["evidence_json"] or {}).get("random_loop") for p_ in plans)
        expected = [random_plan(71 + slot, ["brightness_shift"], 0.0) for slot in range(len(plans))]
        assert [(p_["plan_json"]["family"], p_["plan_json"]["strength"]) for p_ in plans] == [
            (e.family, e.strength) for e in expected
        ]
        assert len({(p_["plan_json"]["family"], p_["plan_json"]["strength"]) for p_ in plans}) >= 2
        planner_usage = list(
            uow.connection.execute(
                select(s.t.usage_events).where(
                    s.t.usage_events.c.experiment_id == UUID(random_id),
                    s.t.usage_events.c.category == "PLANNER",
                )
            ).mappings()
        )
        assert planner_usage == []
    view = (await api.client.get(f"/api/v1/experiments/{random_id}")).json()
    assert view["search_policy"] == "RANDOM_V1"
    assert view["planning_batches"] == []


async def test_review_pause_is_resumable_without_new_baselines(tmp_path):
    class Paused(Adapter):
        def __init__(self):
            super().__init__()
            self.approved = False

        async def tick(self, eid, phase):
            self.log.append((eid, phase))
            return "READY" if phase == "BASELINE" else "COMPLETED" if self.approved else "PAUSED"

    adapter = Paused()
    run = EvaluationRun(tmp_path, protocol(), [{"case_id": "c", "split": "development"}], adapter)
    await run.run()
    assert run.state["status"] == "WAITING_REVIEW"
    ids = [a["experiment_id"] for a in run.state["seeds"][0]["arms"]]
    adapter.approved = True
    await run.run()
    assert run.state["status"] == "COMPLETED"
    assert [a["experiment_id"] for a in run.state["seeds"][0]["arms"]] == ids
    assert sum(phase == "BASELINE" for _, phase in adapter.log) == 3
