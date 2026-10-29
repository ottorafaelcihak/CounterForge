import io
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from counterforge_contracts.vtc import VisualTestContract
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from services.api.app.artifacts import ArtifactStore
from services.api.app.service import Service


def png(mode="RGB", color="red"):
    output = io.BytesIO()
    Image.new(mode, (12, 8), color).save(output, "PNG")
    return output.getvalue()


async def setup_case(api, payload, *, reviewed=True, project_id=None):
    project_id = project_id or api.project_id
    response = await api.client.post(
        f"/api/v1/projects/{project_id}/seeds",
        files={"file": ("seed.png", png(), "image/png")},
        data={
            "question": "How many red containers?",
            "expected_count": 1,
            "license_ack": True,
            "label_reviewer": "Human reviewer",
        },
    )
    assert response.status_code == 201, response.text
    seed = response.json()
    with api.factory() as uow:
        asset = ArtifactStore(uow, api.root).put(
            UUID(project_id), png("L", "white"), "MASK", "image/png", width=12, height=8
        )
        mask_id = uow.connection.scalar(
            uow.tables.seed_masks.insert()
            .values(
                seed_id=UUID(seed["id"]),
                mask_asset_id=asset.id,
                object_label="red container",
                object_ordinal=0,
                geometry_json={"bbox_xyxy": [0, 0, 12, 8]},
                reviewed=reviewed,
                reviewer="Mask reviewer" if reviewed else None,
                reviewed_at=datetime.now(UTC) if reviewed else None,
            )
            .returning(uow.tables.seed_masks.c.id)
        )
        uow.commit()
    payload["case_id"] = seed["case_key"]
    payload["prompt"] = seed["question"]
    payload["expected"] = seed["expected"]
    payload["protected"]["label"] = "red container"
    payload["protected"]["expected_instances"] = 1
    payload["protected"]["mask_asset_ids"] = [str(asset.id)]
    return seed, mask_id, payload


async def contract(api, payload, **kwargs):
    seed, mask, payload = await setup_case(api, payload, **kwargs)
    response = await api.client.post(f"/api/v1/seeds/{seed['id']}/contracts", json=payload)
    assert response.status_code == 201, response.text
    return seed, mask, response.json()


async def target(api, project_id=None):
    response = await api.client.post(
        f"/api/v1/projects/{project_id or api.project_id}/targets",
        json={
            "adapter_key": "LOCAL_FIXTURE",
            "model_id": "LOCAL_FIXTURE",
            "prompt_template": "Return only an integer count.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_contract_requires_exact_seed_masks_and_recorded_review(api, vtc_payload):
    seed, mask, body = await setup_case(api, vtc_payload, reviewed=False)
    path = f"/api/v1/seeds/{seed['id']}/contracts"
    response = await api.client.post(path, json=body)
    assert response.status_code == 409 and response.json()["error"]["code"] == "REVIEW_REQUIRED"
    approved = await api.client.post(
        f"/api/v1/seeds/{seed['id']}/masks/{mask}/approve",
        json={
            "decision": "VALID",
            "reason_code": "MASK_REVIEWED",
            "notes": "Reviewed visible object.",
        },
    )
    assert approved.status_code == 200 and approved.json()["reviewed"] is True
    assert (await api.client.post(path, json=body)).status_code == 201
    wrong_seed = await api.client.post(
        f"/api/v1/seeds/{uuid4()}/masks/{mask}/approve",
        json={"decision": "VALID", "reason_code": "MASK_REVIEWED", "notes": "Reviewed."},
    )
    assert wrong_seed.status_code == 404


async def test_same_database_cross_project_contract_masks_are_forbidden(api, vtc_payload):
    seed, _, body = await setup_case(api, vtc_payload)
    other_project = (
        await api.client.post("/api/v1/projects", json={"name": "Another project"})
    ).json()["id"]
    with api.factory() as uow:
        foreign = ArtifactStore(uow, api.root).put(
            UUID(other_project), png("L", "white"), "MASK", "image/png"
        )
        uow.commit()
    body["protected"]["mask_asset_ids"] = [str(foreign.id)]
    response = await api.client.post(f"/api/v1/seeds/{seed['id']}/contracts", json=body)
    assert response.status_code == 403


async def test_concurrent_contract_revisions_are_immutable_and_canonical(api, vtc_payload):
    seed, _, first = await contract(api, vtc_payload)
    body = VisualTestContract.model_validate(first["contract"])

    def create_revision(_):
        with api.factory() as uow:
            result = Service(uow, api.settings).create_contract(UUID(seed["id"]), body)
            uow.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        revisions = list(pool.map(create_revision, range(2)))
    assert sorted(item.version for item in revisions) == [2, 3]
    assert {item.contract_sha256 for item in revisions} == {first["contract_sha256"]}
    with pytest.raises(DBAPIError), api.factory() as uow:
        uow.connection.execute(
            uow.tables.visual_contracts.update()
            .where(uow.tables.visual_contracts.c.id == UUID(first["id"]))
            .values(contract_json={"tampered": True})
        )
    assert (await api.client.get(f"/api/v1/contracts/{first['id']}")).json() == first


async def test_experiment_locks_snapshot_and_start_is_idempotent(api, vtc_payload):
    seed, mask, vtc = await contract(api, vtc_payload)
    config = await target(api)
    body = {
        "project_id": api.project_id,
        "contract_id": vtc["id"],
        "target_config_id": config["id"],
    }
    headers = {"Idempotency-Key": "first-run"}
    first = await api.client.post("/api/v1/experiments", json=body, headers=headers)
    assert first.status_code == 202, first.text
    assert first.json()["state"] == "BASELINE_VALIDATING"
    assert (
        await api.client.post("/api/v1/experiments", json=body, headers=headers)
    ).json() == first.json()
    changed = await api.client.post(
        "/api/v1/experiments", json={**body, "mode": "RANDOM_BASELINE"}, headers=headers
    )
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    experiment_id = UUID(first.json()["experiment_id"])
    with api.factory() as uow:
        row = (
            uow.connection.execute(
                select(uow.tables.experiments).where(uow.tables.experiments.c.id == experiment_id)
            )
            .mappings()
            .one()
        )
        snapshot = row["config_snapshot"]
        assert snapshot["contract_sha256"] == vtc["contract_sha256"]
        assert snapshot["masks"][0]["reviewer"] == "Mask reviewer"
        assert snapshot["limits"]["max_runtime_seconds"] == 1200
        assert snapshot["verifier_version"] and snapshot["pricing_revision"]
        assert "secret_ref" not in str(snapshot)
        uow.connection.execute(
            uow.tables.seed_masks.update()
            .where(uow.tables.seed_masks.c.id == mask)
            .values(reviewed=False, reviewer=None, reviewed_at=None)
        )
        uow.connection.execute(
            uow.tables.target_configs.update()
            .where(uow.tables.target_configs.c.id == UUID(config["id"]))
            .values(inference_options={"temperature": 1.0})
        )
        uow.commit()
    with api.factory() as uow:
        assert (
            uow.connection.scalar(
                select(uow.tables.experiments.c.config_snapshot).where(
                    uow.tables.experiments.c.id == experiment_id
                )
            )
            == snapshot
        )
    assert (await api.client.get(f"/api/v1/contracts/{vtc['id']}")).json()["locked_at"] is not None
    assert (await api.client.get(f"/api/v1/experiments/{experiment_id}")).status_code == 200


async def test_request_limits_only_tighten_contract_and_foreign_targets_fail(api, vtc_payload):
    vtc_payload["limits"]["max_candidates"] = 4
    _, _, vtc = await contract(api, vtc_payload)
    config = await target(api)
    body = {
        "project_id": api.project_id,
        "contract_id": vtc["id"],
        "target_config_id": config["id"],
    }
    rejected = await api.client.post(
        "/api/v1/experiments", json=body, headers={"Idempotency-Key": "too-big"}
    )
    assert rejected.status_code == 422
    other = (await api.client.post("/api/v1/projects", json={"name": "Another project"})).json()[
        "id"
    ]
    foreign = await target(api, other)
    bad = await api.client.post(
        "/api/v1/experiments",
        json={
            **body,
            "target_config_id": foreign["id"],
            "limits": {**vtc["contract"]["limits"], "max_candidates": 2},
        },
        headers={"Idempotency-Key": "foreign"},
    )
    assert bad.status_code == 403


@pytest.mark.parametrize(
    "extra",
    [
        {"url": "http://127.0.0.1/private"},
        {"secret_ref": "OWNER_AUTH_TOKEN"},
        {"model_id": "https://evil.invalid/target"},
    ],
)
async def test_targets_are_curated_and_never_accept_secret_selection(api, extra):
    body = {
        "adapter_key": "META_SPARK",
        "model_id": "muse-spark-1.3",
        "prompt_template": "Count the containers.",
    }
    response = await api.client.post(
        f"/api/v1/projects/{api.project_id}/targets", json={**body, **extra}
    )
    assert response.status_code == 422
    assert "OWNER_AUTH_TOKEN" not in response.text and "evil.invalid" not in response.text


async def test_job_creation_is_durable_and_idempotency_conflicts_are_explicit(api, vtc_payload):
    seed, _, _ = await setup_case(api, vtc_payload)
    path = f"/api/v1/seeds/{seed['id']}/segment"
    headers = {"Idempotency-Key": "segment-once"}
    first = await api.client.post(path, json={"concept": "red container"}, headers=headers)
    assert first.status_code == 202, first.text
    assert first.json()["state"] == "QUEUED"
    second = await api.client.post(path, json={"concept": "red container"}, headers=headers)
    assert first.json() == second.json()
    changed = await api.client.post(path, json={"concept": "blue container"}, headers=headers)
    assert changed.status_code == 409
    assert (await api.client.get(first.json()["links"]["self"])).json() == first.json()
    with api.factory() as uow:
        rows = uow.connection.execute(select(uow.tables.background_jobs)).mappings().all()
        assert len(rows) == 1 and rows[0]["request_json"]["seed_id"] == seed["id"]
        assert uow.connection.execute(select(uow.tables.usage_events)).all() == []


async def test_public_asset_access_requires_explicit_whitelist(api, vtc_payload):
    seed, _, _ = await setup_case(api, vtc_payload)
    asset_id = UUID(seed["image_asset_id"])
    path = f"/api/v1/assets/{asset_id}/content"
    assert (await api.client.get(path, headers={"Authorization": ""})).status_code == 401
    with api.factory() as uow:
        uow.connection.execute(
            text("INSERT INTO public_asset_whitelist (asset_id,published_by) VALUES (:id,'owner')"),
            {"id": asset_id},
        )
        uow.commit()
    response = await api.client.get(path, headers={"Authorization": ""})
    assert response.status_code == 200 and response.content == png()
    assert (
        await api.client.get(f"/api/v1/assets/{uuid4()}/content", headers={"Authorization": ""})
    ).status_code == 401


async def test_finding_reads_are_sanitized_and_unsealed_legacy_export_is_rejected(api, vtc_payload):
    seed, _, vtc = await contract(api, vtc_payload)
    config = await target(api)
    started = await api.client.post(
        "/api/v1/experiments",
        json={
            "project_id": api.project_id,
            "contract_id": vtc["id"],
            "target_config_id": config["id"],
        },
        headers={"Idempotency-Key": "export-test"},
    )
    exp_id = UUID(started.json()["experiment_id"])
    with api.factory() as uow:
        store = ArtifactStore(uow, api.root)
        image = store.put(UUID(api.project_id), png(color="blue"), "MUTATION_IMAGE", "image/png")
        mutation = uow.connection.scalar(
            uow.tables.mutations.insert()
            .values(
                experiment_id=exp_id,
                image_asset_id=image.id,
                family="brightness_shift",
                strength="0.1",
                generator="DETERMINISTIC",
                recipe_json={},
                state="STABLE",
            )
            .returning(uow.tables.mutations.c.id)
        )
        uow.connection.execute(
            uow.tables.mutation_validations.insert().values(
                mutation_id=mutation,
                verifier_version="0.1.0",
                verdict="VALID",
                evidence_json={"private_url": "https://DO_NOT_LEAK.invalid/signed"},
            )
        )
        finding = uow.connection.scalar(
            uow.tables.findings.insert()
            .values(
                experiment_id=exp_id,
                mutation_id=mutation,
                status="STABLE",
                confirmation_attempts=3,
                confirmation_failures=3,
                summary_json={"secret_ref": "DO_NOT_LEAK"},
            )
            .returning(uow.tables.findings.c.id)
        )
        for attempt in (1, 2, 3):
            uow.connection.execute(
                uow.tables.model_observations.insert().values(
                    experiment_id=exp_id,
                    target_config_id=UUID(config["id"]),
                    mutation_id=mutation,
                    phase="CONFIRMATION",
                    attempt_no=attempt,
                    request_fingerprint="a" * 64,
                    outcome="INCORRECT",
                    parsed_json={"count": 0},
                )
            )
        uow.commit()
    read = await api.client.get(f"/api/v1/findings/{finding}")
    assert read.status_code == 200, read.text
    assert read.json()["ground_truth"]["value"] == 1
    assert len(read.json()["confirmation"]["raw_observation_ids"]) == 3
    assert "DO_NOT_LEAK" not in read.text and "secret_ref" not in read.text
    assert "storage_key" not in read.text
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.target_configs.update()
            .where(uow.tables.target_configs.c.id == UUID(config["id"]))
            .values(model_id="later-model-revision")
        )
        uow.commit()
    assert (await api.client.get(f"/api/v1/findings/{finding}")).json()["model"][
        "model_id"
    ] == "LOCAL_FIXTURE"
    queued = await api.client.post(
        f"/api/v1/findings/{finding}/exports", headers={"Idempotency-Key": "bundle-once"}
    )
    # Legacy count-only rows remain readable but cannot manufacture portable proof.
    assert queued.status_code == 409, queued.text
    with api.factory() as uow:
        assert uow.connection.execute(select(uow.tables.regression_exports)).all() == []
        assert (
            uow.connection.execute(
                select(uow.tables.background_jobs).where(
                    uow.tables.background_jobs.c.kind == "EXPORT"
                )
            ).all()
            == []
        )


async def test_duplicate_blob_writes_and_rollback_leave_only_safe_orphans(api):
    import os
    from datetime import timedelta

    from services.api.app.artifacts import BlobStore

    project = UUID(api.project_id)

    def put(_):
        with api.factory() as uow:
            ref = ArtifactStore(uow, api.root).put(project, png(), "SEED_IMAGE", "image/png")
            uow.commit()
            return ref.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        refs = list(pool.map(put, range(2)))
    assert refs[0] == refs[1]
    with api.factory() as uow:
        orphan = ArtifactStore(uow, api.root).put(
            project, png(color="blue"), "MUTATION_IMAGE", "image/png"
        )
        # Rollback is automatic; file is committed but must never become a DB reference.
    os.utime(api.root / orphan.storage_key, (1, 1))
    with api.factory() as uow:
        registered = set(uow.connection.scalars(select(uow.tables.artifact_assets.c.storage_key)))
        assert len(registered) == 1
    blobs = BlobStore(api.root)
    assert blobs.orphans(registered, datetime.now(UTC) - timedelta(days=1)) == [orphan.storage_key]
    assert (api.root / orphan.storage_key).exists()
