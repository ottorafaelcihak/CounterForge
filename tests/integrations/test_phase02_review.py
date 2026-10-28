"""Phase 02 review regressions for evidence integrity and authorization."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from services.api.app.artifacts import ArtifactStore, BlobStore
from tests.integrations.test_contracts_and_jobs import png, target
from tests.integrations.test_seed_api import upload


async def prepared_case(api, payload):
    seed = (await upload(api)).json()
    with api.factory() as uow:
        asset = ArtifactStore(uow, api.root).put(
            UUID(api.project_id), png("L", "white"), "MASK", "image/png", width=12, height=8
        )
        mask_id = uow.connection.scalar(
            uow.tables.seed_masks.insert()
            .values(
                seed_id=UUID(seed["id"]),
                mask_asset_id=asset.id,
                object_label="red container",
                object_ordinal=0,
                geometry_json={},
                reviewed=True,
                reviewer="Mask reviewer",
                reviewed_at=datetime.now(UTC),
            )
            .returning(uow.tables.seed_masks.c.id)
        )
        uow.commit()
    payload["case_id"] = seed["case_key"]
    payload["prompt"] = seed["question"]
    payload["expected"] = seed["expected"]
    payload["protected"].update(
        label="red container", expected_instances=1, mask_asset_ids=[str(asset.id)]
    )
    return seed, mask_id, payload


async def new_contract(api, seed, payload):
    response = await api.client.post(f"/api/v1/seeds/{seed['id']}/contracts", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def new_run(api, vtc, key="review-run"):
    config = await target(api)
    return await api.client.post(
        "/api/v1/experiments",
        json={
            "project_id": api.project_id,
            "contract_id": vtc["id"],
            "target_config_id": config["id"],
        },
        headers={"Idempotency-Key": key},
    )


async def test_revoked_mask_approval_blocks_a_new_start_without_changing_history(api, vtc_payload):
    seed, mask, payload = await prepared_case(api, vtc_payload)
    vtc = await new_contract(api, seed, payload)
    reviewed = await api.client.post(
        f"/api/v1/seeds/{seed['id']}/masks/{mask}/approve",
        json={
            "decision": "INVALID",
            "reason_code": "WRONG_MASK",
            "notes": "Mask covers the wrong object.",
        },
    )
    assert reviewed.status_code == 200
    response = await new_run(api, vtc)
    assert response.status_code == 409 and response.json()["error"]["code"] == "REVIEW_REQUIRED"
    assert (await api.client.get(f"/api/v1/contracts/{vtc['id']}")).json()["locked_at"] is None
    with api.factory() as uow:
        assert uow.connection.execute(select(uow.tables.experiments)).all() == []


async def test_run_snapshot_captures_the_exact_approval_at_start(api, vtc_payload):
    seed, mask, payload = await prepared_case(api, vtc_payload)
    vtc = await new_contract(api, seed, payload)
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.seed_masks.update()
            .where(uow.tables.seed_masks.c.id == mask)
            .values(
                reviewer="Second reviewer",
                reviewed_at=datetime.now(UTC),
            )
        )
        uow.commit()
    response = await new_run(api, vtc)
    assert response.status_code == 202
    with api.factory() as uow:
        snapshot = uow.connection.scalar(select(uow.tables.experiments.c.config_snapshot))
    assert snapshot["masks"][0]["reviewer"] == "Second reviewer"
    assert snapshot["masks"][0]["object_label"] == "red container"


@pytest.mark.parametrize("route", ["create", "validate"])
async def test_contract_masks_must_match_the_protected_label(api, vtc_payload, route):
    seed, _, payload = await prepared_case(api, vtc_payload)
    payload["protected"]["label"] = "green cup"
    path = (
        f"/api/v1/seeds/{seed['id']}/contracts"
        if route == "create"
        else "/api/v1/contracts/validate"
    )
    response = await api.client.post(path, json=payload)
    assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_INPUT"


@pytest.mark.parametrize("stage", ["contract", "start"])
async def test_original_evidence_tampering_blocks_contract_or_run_lock(api, vtc_payload, stage):
    seed, _, payload = await prepared_case(api, vtc_payload)
    assert seed["provenance"]["original_asset_id"] != seed["image_asset_id"]
    vtc = await new_contract(api, seed, payload) if stage == "start" else None
    with api.factory() as uow:
        key = uow.connection.scalar(
            select(uow.tables.artifact_assets.c.storage_key).where(
                uow.tables.artifact_assets.c.id == UUID(seed["provenance"]["original_asset_id"])
            )
        )
    (api.root / key).write_bytes(b"original evidence changed")
    response = (
        await new_run(api, vtc)
        if stage == "start"
        else await api.client.post(f"/api/v1/seeds/{seed['id']}/contracts", json=payload)
    )
    assert (
        response.status_code == 500
        and response.json()["error"]["code"] == "ARTIFACT_CHECKSUM_MISMATCH"
    )


async def test_mask_audit_keeps_readable_rationale_and_redacts_credentials(api, vtc_payload):
    seed, mask, _ = await prepared_case(api, vtc_payload)
    response = await api.client.post(
        f"/api/v1/seeds/{seed['id']}/masks/{mask}/approve",
        json={
            "decision": "VALID",
            "reason_code": "HUMAN_MASK_REVIEW",
            "notes": 'Reviewed count. Bearer phase02-test-owner-token https://private.invalid/signed?token=secret access_token=PRIVATE_ACCESS client_secret=PRIVATE_CLIENT AWS_SECRET_ACCESS_KEY=PRIVATE_AWS "refresh_token": "PRIVATE_REFRESH" password="PRIVATE PASSWORD"',
        },
    )
    assert response.status_code == 200
    with api.factory() as uow:
        details = uow.connection.scalar(
            select(uow.tables.audit_events.c.details_json).where(
                uow.tables.audit_events.c.action == "MASK_REVIEWED"
            )
        )
    assert details.get("notes", "").startswith("Reviewed count.")
    assert details["reason_code"] == "HUMAN_MASK_REVIEW"
    assert "phase02-test-owner-token" not in str(details) and "private.invalid" not in str(details)
    assert "PRIVATE" not in str(details)


async def test_upload_question_is_normalized_before_persisting_the_seed(api):
    response = await upload(api, question="  How many containers are visible?  ")
    assert response.status_code == 201
    assert response.json()["question"] == "How many containers are visible?"
    invalid = await upload(api, question=" " * 20)
    assert invalid.status_code == 422
    too_short = await upload(api, question=" " * 10 + "Count?" + " " * 10)
    assert too_short.status_code == 422


async def test_stream_bound_stops_consuming_before_a_large_multipart_file_is_spooled(api):
    sent = 0

    async def body():
        nonlocal sent
        yield b'--upload-boundary\r\nContent-Disposition: form-data; name="file"; filename="big.png"\r\nContent-Type: image/png\r\n\r\n'
        for _ in range(20):
            sent += 1
            yield b"x" * 1024 * 1024
        yield b"\r\n--upload-boundary--\r\n"

    response = await api.client.post(
        f"/api/v1/projects/{api.project_id}/seeds",
        content=body(),
        headers={
            "Content-Type": "multipart/form-data; boundary=upload-boundary",
        },
    )
    assert response.status_code == 422
    assert sent < 20
    assert list(api.root.iterdir()) == []


async def test_orphan_maintenance_cannot_delete_a_blob_being_registered(api):
    project = UUID(api.project_id)
    blob = BlobStore(api.root).put(project, png(), "image/png")
    os.utime(api.root / blob.storage_key, (1, 1))
    with api.factory() as writer:
        ref = ArtifactStore(writer, api.root).put(project, png(), "SEED_IMAGE", "image/png")

        def collect():
            try:
                with api.factory() as maintenance:
                    maintenance.connection.execute(text("SET LOCAL lock_timeout = '100ms'"))
                    result = ArtifactStore(maintenance, api.root).maintain_orphans(
                        datetime.now(UTC) - timedelta(days=1), delete=True
                    )
                    maintenance.commit()
                    return result
            except DBAPIError:
                return None

        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(collect).result(timeout=5) in (None, [])
        assert ArtifactStore(writer, api.root).read_verified(ref.id) == png()
        writer.commit()
    with api.factory() as uow:
        assert (
            ArtifactStore(uow, api.root).maintain_orphans(
                datetime.now(UTC) - timedelta(days=1), delete=True
            )
            == []
        )
        assert ArtifactStore(uow, api.root).read_verified(ref.id) == png()


async def test_experiment_metrics_reflect_recorded_candidate_and_finding_evidence(api, vtc_payload):
    seed, _, payload = await prepared_case(api, vtc_payload)
    vtc = await new_contract(api, seed, payload)
    response = await new_run(api, vtc)
    exp_id = UUID(response.json()["experiment_id"])
    with api.factory() as uow:
        image = ArtifactStore(uow, api.root).put(
            UUID(api.project_id), png(color="blue"), "MUTATION_IMAGE", "image/png"
        )
        stable = None
        for state, verdict in (
            ("STABLE", "VALID"),
            ("INVALID", "INVALID"),
            ("REVIEW_REQUIRED", "REVIEW_REQUIRED"),
        ):
            mutation = uow.connection.scalar(
                uow.tables.mutations.insert()
                .values(
                    experiment_id=exp_id,
                    image_asset_id=image.id,
                    family="brightness_shift",
                    strength="0.1",
                    generator="DETERMINISTIC",
                    recipe_json={},
                    state=state,
                )
                .returning(uow.tables.mutations.c.id)
            )
            uow.connection.execute(
                uow.tables.mutation_validations.insert().values(
                    mutation_id=mutation,
                    verifier_version="0.1.0",
                    verdict=verdict,
                    evidence_json={},
                )
            )
            if state == "STABLE":
                stable = mutation
        uow.connection.execute(
            uow.tables.findings.insert().values(
                experiment_id=exp_id,
                mutation_id=stable,
                status="STABLE",
                confirmation_attempts=3,
                confirmation_failures=3,
            )
        )
        target_id = uow.connection.scalar(
            select(uow.tables.experiments.c.target_config_id).where(
                uow.tables.experiments.c.id == exp_id
            )
        )
        for phase, count in (("SCREENING", 1), ("CONFIRMATION", 3)):
            for attempt in range(1, count + 1):
                uow.connection.execute(
                    uow.tables.model_observations.insert().values(
                        experiment_id=exp_id,
                        mutation_id=stable,
                        target_config_id=target_id,
                        phase=phase,
                        attempt_no=attempt,
                        request_fingerprint="a" * 64,
                        outcome="INCORRECT",
                        parsed_json={"count": 0},
                    )
                )
        uow.connection.execute(
            uow.tables.experiments.update()
            .where(uow.tables.experiments.c.id == exp_id)
            .values(candidates_created=3, calls_consumed=4)
        )
        uow.commit()
    metrics = (await api.client.get(f"/api/v1/experiments/{exp_id}")).json()["metrics"]
    assert metrics["generated_candidates"] == 3
    assert metrics["valid_candidates"] == 1
    assert metrics["invalid_candidates"] == 1
    assert metrics["review_required_candidates"] == 1
    assert metrics["stable_findings"] == 1 and metrics["valid_screening_calls"] == 1
    assert metrics["total_target_calls"] == 4


async def test_unknown_provider_reason_does_not_persist_private_headers_or_urls(api, vtc_payload):
    from decimal import Decimal

    from services.worker.app.budget import Budget

    seed, _, payload = await prepared_case(api, vtc_payload)
    vtc = await new_contract(api, seed, payload)
    response = await new_run(api, vtc)
    exp_id = UUID(response.json()["experiment_id"])
    with api.factory() as uow:
        budget = Budget(uow)
        budget.reserve_operation(exp_id, "private-reason", "TARGET", Decimal("0.1"), 1, 0)
        budget.begin_operation("private-reason")
        budget.mark_unknown_charge(
            "private-reason",
            "Bearer PRIVATE_TEST_TOKEN https://private.invalid/signed?token=secret",
        )
        uow.commit()
    with api.factory() as uow:
        recorded = [
            dict(row)
            for table in (
                uow.tables.run_operations,
                uow.tables.experiment_events,
                uow.tables.audit_events,
            )
            for row in uow.connection.execute(select(table)).mappings()
        ]
    assert "PRIVATE_TEST_TOKEN" not in str(recorded) and "private.invalid" not in str(recorded)


async def test_identical_bytes_can_have_distinct_evidence_roles_without_rewriting_identity(api):
    with api.factory() as uow:
        store = ArtifactStore(uow, api.root)
        seed = store.put(UUID(api.project_id), png(), "SEED_IMAGE", "image/png", width=12, height=8)
        mask = store.put(UUID(api.project_id), png(), "MASK", "image/png", width=12, height=8)
        assert seed.id != mask.id and seed.storage_key == mask.storage_key
        assert store.put(UUID(api.project_id), png(), "MASK", "image/png").id == mask.id
        assert store.read_verified(seed.id) == store.read_verified(mask.id) == png()
        uow.commit()
