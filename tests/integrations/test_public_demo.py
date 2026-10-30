"""Public demo snapshot isolation: sanitized DTO, hash binding, zero dispatch.

All evidence here is synthesized fixture provenance with an explicit fixture
audit actor; nothing reads retained live assets or implies real Meta results.
"""

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx2
import pytest
from pydantic import SecretStr
from sqlalchemy import func, null, select, text

from services.api.app.config import Settings
from services.api.app.main import create_app
from tests.integrations.test_contracts_and_jobs import contract, target

INTENT = "Publish this recorded snapshot for the public read-only demo."
PRIVATE_MARKERS = (
    "prompt_template",
    "fixture-reviewer",
    "Human reviewer",
    "storage_key",
    "raw_response",
)


@pytest.fixture
async def fixture_client(api, migrated_engine):
    """Explicit fixture/test mode client; anonymous unless a test adds auth."""

    settings = Settings(
        app_env="test",
        provider_mode="FIXTURE",
        allow_synthetic_fixtures=True,
        artifact_root=api.root,
        owner_auth_token=SecretStr("phase02-test-owner-token"),
        web_origin="http://localhost:3000",
    )
    application = create_app(settings=settings, engine=migrated_engine)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client


def owner(api) -> dict[str, str]:
    return {"Authorization": "Bearer [REDACTED]"}


@pytest.fixture(autouse=True)
def simulated_evidence(api, monkeypatch):
    api.demo_monkeypatch = monkeypatch


async def synthesized_run(api, vtc_payload) -> SimpleNamespace:
    """Full retained simulated pipeline, including bound review and canonical job."""
    from services.api.app.service import Service
    from tests.integrations.test_exports import stable

    previous_mode, previous_fixtures = (
        api.settings.provider_mode,
        api.settings.allow_synthetic_fixtures,
    )
    try:
        fid, mutation = await stable(api, vtc_payload, api.demo_monkeypatch)
    finally:
        api.settings.provider_mode, api.settings.allow_synthetic_fixtures = (
            previous_mode,
            previous_fixtures,
        )
    with api.factory() as uow:
        service = Service(uow, api.settings)
        finding = service.finding(UUID(fid))
        experiment = service.experiment(finding["experiment_id"])
        contract_row = service.contract(experiment["contract_id"])
        seed = service.row(uow.tables.seed_cases, contract_row["seed_id"])
        report = service.candidates.checked_report(service.candidates.row(UUID(mutation["id"])))
        uow.connection.execute(
            uow.tables.findings.update()
            .where(uow.tables.findings.c.id == UUID(fid))
            .values(
                demo_approved_by="fixture-reviewer",
                demo_approved_at=datetime.now(UTC),
                demo_validation_sha256=report.report_sha256,
            )
        )
        run = SimpleNamespace(
            experiment_id=experiment["id"],
            project_id=experiment["project_id"],
            contract_id=experiment["contract_id"],
            target_id=experiment["target_config_id"],
            seed_id=seed["id"],
            seed_asset_id=seed["image_asset_id"],
            mutation_id=UUID(mutation["id"]),
            mutated_asset_id=UUID(mutation["assets"]["candidate"]),
            finding_id=UUID(fid),
            report_sha256=report.report_sha256,
            mutated_bytes=service.artifacts.read_verified(UUID(mutation["assets"]["candidate"])),
            seed_bytes=service.artifacts.read_verified(seed["image_asset_id"]),
            retained_counts={
                name: uow.connection.scalar(select(func.count()).select_from(table))
                for name, table in (
                    ("usage_events", uow.tables.usage_events),
                    ("background_jobs", uow.tables.background_jobs),
                )
            },
        )
        uow.commit()
    return run


async def prepare_and_publish(api, run: SimpleNamespace) -> dict:
    prepared = await api.client.post(f"/api/v1/experiments/{run.experiment_id}/demo-snapshot")
    assert prepared.status_code == 201, prepared.text
    snapshot = prepared.json()
    assert snapshot["status"] == "DRAFT"
    published = await api.client.post(
        f"/api/v1/demo-snapshots/{snapshot['id']}/publish",
        json={
            "publication_intent": INTENT,
            "confirm_publication": True,
            "redistribution_rights_acknowledged": True,
            "manifest_sha256": snapshot["manifest_sha256"],
        },
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "PUBLISHED"
    return published.json()


async def test_default_demo_is_empty_anonymous_and_query_cannot_choose_data(api):
    response = await api.client.get("/api/v1/public/demo", headers={"Authorization": ""})
    assert response.status_code == 200
    manifest = response.json()
    assert manifest["state"] == "EMPTY" and manifest["findings"] == [] and manifest["assets"] == []
    assert manifest["snapshot_id"] is None and manifest["metrics"] is None
    changed = await api.client.get(
        f"/api/v1/public/demo?experiment_id={uuid4()}&asset_id={uuid4()}",
        headers={"Authorization": ""},
    )
    assert changed.json() == manifest
    for marker in PRIVATE_MARKERS:
        assert marker not in response.text


async def test_prepare_requires_owner_and_approved_findings(api, vtc_payload):
    run = await synthesized_run(api, vtc_payload)
    anonymous = await api.client.post(
        f"/api/v1/experiments/{run.experiment_id}/demo-snapshot", headers={"Authorization": ""}
    )
    assert anonymous.status_code == 401
    # Remove the approval: preparation must fail closed, never invent one.
    with api.factory() as uow:
        uow.connection.execute(
            text("UPDATE findings SET demo_approved_at=NULL, demo_approved_by=NULL WHERE id=:id"),
            {"id": run.finding_id},
        )
        uow.commit()
    denied = await api.client.post(f"/api/v1/experiments/{run.experiment_id}/demo-snapshot")
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "REVIEW_REQUIRED"
    assert (await api.client.get("/api/v1/public/demo", headers={"Authorization": ""})).json()[
        "state"
    ] == "EMPTY"


async def test_fixture_gallery_is_sanitized_isolated_and_dispatches_nothing(
    api, fixture_client, vtc_payload
):
    run = await synthesized_run(api, vtc_payload)
    snapshot = await prepare_and_publish(api, run)
    # Preparation plus explicit publication intent is audited privately.
    with api.factory() as uow:
        actions = list(
            uow.connection.scalars(
                select(uow.tables.audit_events.c.action).where(
                    uow.tables.audit_events.c.entity_id == snapshot["id"]
                )
            )
        )
        assert "DEMO_SNAPSHOT_PREPARED" in actions and "DEMO_SNAPSHOT_PUBLISHED" in actions
        assert (
            uow.connection.scalar(select(func.count()).select_from(uow.tables.usage_events))
            == run.retained_counts["usage_events"]
        )
        assert (
            uow.connection.scalar(select(func.count()).select_from(uow.tables.background_jobs))
            == run.retained_counts["background_jobs"]
        )
    admin = await api.client.get(f"/api/v1/demo-snapshots/{snapshot['id']}")
    assert admin.json()["source_report_sha256"] == [run.report_sha256]
    assert len(admin.json()["source_experiment_sha256"]) == 64
    assert admin.json()["publication_intent"] == INTENT
    assert (await fixture_client.get(f"/api/v1/demo-snapshots/{snapshot['id']}")).status_code == 401

    manifest_response = await fixture_client.get("/api/v1/public/demo")
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["state"] == "FIXTURE_PREVIEW" and manifest["provenance"] == "LOCAL_FIXTURE"
    assert len(manifest["findings"]) == 1 and len(manifest["assets"]) == 2
    assert manifest["metrics"]["stable_findings"] == 1
    public_ids = {asset["id"] for asset in manifest["assets"]}
    private_ids = {
        str(run.experiment_id),
        str(run.project_id),
        str(run.contract_id),
        str(run.target_id),
        str(run.seed_id),
        str(run.seed_asset_id),
        str(run.mutation_id),
        str(run.mutated_asset_id),
        str(run.finding_id),
    }
    assert public_ids.isdisjoint(private_ids)
    assert manifest["findings"][0]["id"] not in private_ids
    for marker in (*PRIVATE_MARKERS, *private_ids, INTENT):
        assert marker not in manifest_response.text
    changed = await fixture_client.get(f"/api/v1/public/demo?snapshot_id={uuid4()}")
    assert changed.json()["snapshot_id"] == manifest["snapshot_id"]

    by_role = {asset["role"]: asset for asset in manifest["assets"]}
    for role, expected in (("ORIGINAL", run.seed_bytes), ("MUTATED", run.mutated_bytes)):
        asset = by_role[role]
        response = await fixture_client.get(f"/api/v1/public/demo/assets/{asset['id']}/content")
        assert response.status_code == 200 and response.content == expected
        assert hashlib.sha256(response.content).hexdigest() == asset["sha256"]
    # Guessed private IDs and unknown public IDs are denied anonymously.
    for guessed in (run.seed_asset_id, run.mutated_asset_id, run.finding_id, uuid4()):
        assert (
            await fixture_client.get(f"/api/v1/public/demo/assets/{guessed}/content")
        ).status_code == 404
        assert (await fixture_client.get(f"/api/v1/assets/{guessed}/content")).status_code == 401
    # Legacy whitelist rows no longer grant anonymous private access.
    with api.factory() as uow:
        uow.connection.execute(
            text("INSERT INTO public_asset_whitelist (asset_id,published_by) VALUES (:id,'owner')"),
            {"id": run.seed_asset_id},
        )
        uow.commit()
    assert (
        await fixture_client.get(f"/api/v1/assets/{run.seed_asset_id}/content")
    ).status_code == 401
    assert (await api.client.get(f"/api/v1/assets/{run.seed_asset_id}/content")).status_code == 200
    # Anonymous writes and private reads stay denied.
    assert (await fixture_client.post("/api/v1/public/demo", json={})).status_code in {404, 405}
    assert (
        await fixture_client.post(f"/api/v1/findings/{run.finding_id}/exports", json={})
    ).status_code == 401
    assert (await fixture_client.get(f"/api/v1/experiments/{run.experiment_id}")).status_code == 401
    with api.factory() as uow:
        for name in ("usage_events", "background_jobs"):
            assert (
                uow.connection.scalar(select(func.count()).select_from(getattr(uow.tables, name)))
                == run.retained_counts[name]
            )


async def test_fixture_snapshot_fails_closed_outside_fixture_mode(api, vtc_payload):
    run = await synthesized_run(api, vtc_payload)
    await prepare_and_publish(api, run)
    manifest = await api.client.get("/api/v1/public/demo", headers={"Authorization": ""})
    assert manifest.json()["state"] == "EMPTY" and manifest.json()["findings"] == []
    snapshot = await api.client.get("/api/v1/demo-snapshots")
    assert snapshot.status_code in {404, 405}


async def test_revoked_snapshot_assets_are_denied(api, fixture_client, vtc_payload):
    run = await synthesized_run(api, vtc_payload)
    snapshot = await prepare_and_publish(api, run)
    manifest = (await fixture_client.get("/api/v1/public/demo")).json()
    asset_id = manifest["assets"][0]["id"]
    assert (
        await fixture_client.get(f"/api/v1/public/demo/assets/{asset_id}/content")
    ).status_code == 200
    revoked = await api.client.post(f"/api/v1/demo-snapshots/{snapshot['id']}/revoke")
    assert revoked.status_code == 200 and revoked.json()["status"] == "REVOKED"
    assert (await fixture_client.get("/api/v1/public/demo")).json()["state"] == "FIXTURE_PREVIEW"
    assert (await fixture_client.get("/api/v1/public/demo")).json()["findings"] == []
    assert (
        await fixture_client.get(f"/api/v1/public/demo/assets/{asset_id}/content")
    ).status_code == 404
    # Publishing requires explicit intent, rights acknowledgement and the
    # exact prepared manifest hash; a stale hash is denied, not republished.
    assert (
        await api.client.post(f"/api/v1/demo-snapshots/{snapshot['id']}/publish", json={})
    ).status_code == 422
    stale = await api.client.post(
        f"/api/v1/demo-snapshots/{snapshot['id']}/publish",
        json={
            "publication_intent": INTENT,
            "confirm_publication": True,
            "redistribution_rights_acknowledged": True,
            "manifest_sha256": "0" * 64,
        },
    )
    assert stale.status_code == 409
    assert (await fixture_client.get("/api/v1/public/demo")).json()["findings"] == []


async def test_tampered_image_and_report_hash_fail_closed(api, fixture_client, vtc_payload):
    run = await synthesized_run(api, vtc_payload)
    await prepare_and_publish(api, run)
    manifest = (await fixture_client.get("/api/v1/public/demo")).json()
    assert len(manifest["findings"]) == 1
    with api.factory() as uow:
        uow.connection.execute(
            text("UPDATE findings SET demo_validation_sha256=:hash WHERE id=:id"),
            {"hash": "0" * 64, "id": run.finding_id},
        )
        uow.commit()
    stale = (await fixture_client.get("/api/v1/public/demo")).json()
    assert stale["findings"] == [] and stale["assets"] == []
    asset_id = manifest["assets"][0]["id"]
    assert (
        await fixture_client.get(f"/api/v1/public/demo/assets/{asset_id}/content")
    ).status_code == 404


async def test_tampered_snapshot_manifest_fails_closed(api, fixture_client, vtc_payload):
    run = await synthesized_run(api, vtc_payload)
    snapshot = await prepare_and_publish(api, run)
    manifest = (await fixture_client.get("/api/v1/public/demo")).json()
    asset_id = manifest["assets"][0]["id"]
    with api.factory() as uow:
        uow.connection.execute(
            text(
                "UPDATE public_demo_snapshots SET manifest_json "
                "= jsonb_set(manifest_json,'{limitations}','[\"changed\"]') WHERE id=:id"
            ),
            {"id": snapshot["id"]},
        )
        uow.commit()
    assert (await fixture_client.get("/api/v1/public/demo")).json()["findings"] == []
    assert (
        await fixture_client.get(f"/api/v1/public/demo/assets/{asset_id}/content")
    ).status_code == 404


async def test_zero_finding_terminal_run_prepares_honest_empty_snapshot(
    api, fixture_client, vtc_payload
):
    api.settings.provider_mode = "FIXTURE"
    api.settings.allow_synthetic_fixtures = True
    seed, _, vtc = await contract(api, vtc_payload)
    config = await target(api)
    started = await api.client.post(
        "/api/v1/experiments",
        json={
            "project_id": api.project_id,
            "contract_id": vtc["id"],
            "target_config_id": config["id"],
        },
        headers={"Idempotency-Key": "demo-zero-finding-run"},
    )
    assert started.status_code == 202, started.text
    experiment_id = started.json()["experiment_id"]
    # A non-terminal run with no approved findings still fails closed.
    assert (
        await api.client.post(f"/api/v1/experiments/{experiment_id}/demo-snapshot", json={})
    ).status_code == 409
    with api.factory() as uow:
        uow.connection.execute(
            text("UPDATE experiments SET state='COMPLETED' WHERE id=:id"), {"id": experiment_id}
        )
        uow.commit()
    prepared = await api.client.post(
        f"/api/v1/experiments/{experiment_id}/demo-snapshot",
        json={"include_seed_image": True},
    )
    assert prepared.status_code == 201, prepared.text
    snapshot = prepared.json()
    assert snapshot["finding_count"] == 0 and snapshot["asset_count"] == 1
    published = await api.client.post(
        f"/api/v1/demo-snapshots/{snapshot['id']}/publish",
        json={
            "publication_intent": INTENT,
            "confirm_publication": True,
            "redistribution_rights_acknowledged": True,
            "manifest_sha256": snapshot["manifest_sha256"],
        },
    )
    assert published.status_code == 200, published.text
    manifest = (await fixture_client.get("/api/v1/public/demo")).json()
    assert manifest["findings"] == [] and manifest["metrics"]["stable_findings"] == 0
    assert any("No confirmed finding" in line for line in manifest["limitations"])
    assert len(manifest["assets"]) == 1
    asset_id = manifest["assets"][0]["id"]
    assert (
        await fixture_client.get(f"/api/v1/public/demo/assets/{asset_id}/content")
    ).status_code == 200
    del seed


async def test_draft_revoke_and_owner_scope(api, vtc_payload):
    run = await synthesized_run(api, vtc_payload)
    prepared = await api.client.post(f"/api/v1/experiments/{run.experiment_id}/demo-snapshot")
    assert prepared.status_code == 201, prepared.text
    snapshot_id = prepared.json()["id"]
    # Another owner's label must not read or revoke this snapshot.
    with api.factory() as uow:
        uow.connection.execute(
            text("UPDATE projects SET owner_label='other-owner' WHERE id=:id"),
            {"id": run.project_id},
        )
        uow.commit()
    assert (await api.client.get(f"/api/v1/demo-snapshots/{snapshot_id}")).status_code == 403
    assert (
        await api.client.post(f"/api/v1/demo-snapshots/{snapshot_id}/revoke")
    ).status_code == 403
    with api.factory() as uow:
        uow.connection.execute(
            text("UPDATE projects SET owner_label='local-owner' WHERE id=:id"),
            {"id": run.project_id},
        )
        uow.commit()
    # Revoking an unpublished DRAFT is valid and immediately terminal.
    revoked = await api.client.post(f"/api/v1/demo-snapshots/{snapshot_id}/revoke")
    assert revoked.status_code == 200 and revoked.json()["status"] == "REVOKED"


async def test_second_publish_supersedes_first_and_old_assets_are_denied(
    api, fixture_client, vtc_payload
):
    run = await synthesized_run(api, vtc_payload)
    first = await prepare_and_publish(api, run)
    first_manifest = (await fixture_client.get("/api/v1/public/demo")).json()
    first_asset = first_manifest["assets"][0]["id"]
    second_prepared = await api.client.post(
        f"/api/v1/experiments/{run.experiment_id}/demo-snapshot"
    )
    assert second_prepared.status_code == 201, second_prepared.text
    second = second_prepared.json()
    published = await api.client.post(
        f"/api/v1/demo-snapshots/{second['id']}/publish",
        json={
            "publication_intent": INTENT,
            "confirm_publication": True,
            "redistribution_rights_acknowledged": True,
            "manifest_sha256": second["manifest_sha256"],
        },
    )
    assert published.status_code == 200, published.text
    with api.factory() as uow:
        statuses = dict(
            uow.connection.execute(
                select(
                    uow.tables.public_demo_snapshots.c.id,
                    uow.tables.public_demo_snapshots.c.status,
                )
            ).all()
        )
    assert statuses[UUID(first["id"])] == "SUPERSEDED"
    assert statuses[UUID(second["id"])] == "PUBLISHED"
    assert (
        await fixture_client.get(f"/api/v1/public/demo/assets/{first_asset}/content")
    ).status_code == 404
    current = (await fixture_client.get("/api/v1/public/demo")).json()
    assert current["snapshot_id"] == second["id"]


async def test_multi_finding_snapshot_shares_seed_image(api, fixture_client, vtc_payload):
    run = await synthesized_run(api, vtc_payload)
    # Execute a distinct second candidate through the same simulated pipeline.
    from services.worker.app.experiments import execute_experiment
    from tests.integrations.test_candidates import BRIGHT, create, review_body
    from tests.integrations.test_experiment_pipeline import fixture_settings, replies

    second = await create(
        api, str(run.experiment_id), key="demo-second", plan={**BRIGHT, "strength": 0.5}
    )
    assert second.status_code == 201, second.text
    mutation = second.json()
    reviewed = await api.client.post(
        f"/api/v1/mutations/{mutation['id']}/review", json=review_body(mutation)
    )
    assert reviewed.status_code == 200, reviewed.text
    replies(api.demo_monkeypatch, [0, 0, 0, 0])
    for _ in range(4):
        await execute_experiment(api.factory, fixture_settings(api), run.experiment_id)
    with api.factory() as uow:
        findings = uow.tables.findings
        second_finding = (
            uow.connection.execute(
                select(findings).where(findings.c.mutation_id == UUID(mutation["id"]))
            )
            .mappings()
            .one()
        )
        assert second_finding["status"] == "STABLE"
        uow.connection.execute(
            findings.update()
            .where(findings.c.id == second_finding["id"])
            .values(
                demo_approved_by="fixture-reviewer",
                demo_approved_at=datetime.now(UTC),
                demo_validation_sha256=reviewed.json()["validation"]["report_sha256"],
            )
        )
        uow.commit()
    snapshot = await prepare_and_publish(api, run)
    assert snapshot["finding_count"] == 2
    manifest = (await fixture_client.get("/api/v1/public/demo")).json()
    assert len(manifest["findings"]) == 2
    originals = [a for a in manifest["assets"] if a["role"] == "ORIGINAL"]
    assert len(originals) == 1  # shared seed image, not a duplicate row
    assert all(originals[0]["id"] in f["asset_ids"] for f in manifest["findings"])


async def test_seed_only_asset_fails_closed_when_source_binding_changes(
    api, fixture_client, vtc_payload
):
    api.settings.provider_mode = "FIXTURE"
    api.settings.allow_synthetic_fixtures = True
    seed, _, vtc = await contract(api, vtc_payload)
    del seed
    config = await target(api)
    started = await api.client.post(
        "/api/v1/experiments",
        json={
            "project_id": api.project_id,
            "contract_id": vtc["id"],
            "target_config_id": config["id"],
        },
        headers={"Idempotency-Key": "demo-seed-only-stale"},
    )
    experiment_id = started.json()["experiment_id"]
    with api.factory() as uow:
        uow.connection.execute(
            text("UPDATE experiments SET state='COMPLETED' WHERE id=:id"), {"id": experiment_id}
        )
        uow.commit()
    prepared = await api.client.post(
        f"/api/v1/experiments/{experiment_id}/demo-snapshot",
        json={"include_seed_image": True},
    )
    assert prepared.status_code == 201, prepared.text
    snapshot = prepared.json()
    assert snapshot["finding_count"] == 0
    published = await api.client.post(
        f"/api/v1/demo-snapshots/{snapshot['id']}/publish",
        json={
            "publication_intent": INTENT,
            "confirm_publication": True,
            "redistribution_rights_acknowledged": True,
            "manifest_sha256": snapshot["manifest_sha256"],
        },
    )
    assert published.status_code == 200, published.text
    manifest = (await fixture_client.get("/api/v1/public/demo")).json()
    asset_id = manifest["assets"][0]["id"]
    assert (
        await fixture_client.get(f"/api/v1/public/demo/assets/{asset_id}/content")
    ).status_code == 200
    # Tamper with the stored snapshot binding itself: the seed image must
    # stop serving even though its own bytes and the immutable source
    # experiment config_snapshot are unchanged. (Updating config_snapshot
    # would correctly raise the immutable_experiment_snapshot trigger, so
    # the corruption target is the snapshot's private binding, which
    # _verify_bindings re-checks on every public read.)
    with api.factory() as uow:
        uow.connection.execute(
            text(
                "UPDATE public_demo_snapshots SET private_bindings_json "
                "= jsonb_set(private_bindings_json,'{source_experiment_sha256}',"
                "'\"0000000000000000000000000000000000000000000000000000000000000000\"') "
                "WHERE id=:id"
            ),
            {"id": snapshot["id"]},
        )
        uow.commit()
    empty = (await fixture_client.get("/api/v1/public/demo")).json()
    assert empty["snapshot_id"] is None
    assert empty["findings"] == []
    assert empty["assets"] == []
    assert (
        await fixture_client.get(f"/api/v1/public/demo/assets/{asset_id}/content")
    ).status_code == 404


def simulated_real_source(api, source, defect):
    """Copy retained simulated bytes into LIVE-pinned negative-test rows only.

    Never execute or publish this synthetic REAL_PROVIDER-labelled source.
    This tests production provenance gates without network or live evidence.
    Immutable source evidence and triggers remain untouched.
    """
    from copy import deepcopy

    from counterforge_contracts.responses import ValidationReport
    from counterforge_verification.invariants import seal_report

    from services.api.app.service import Service, canonical_json, fingerprint
    from services.worker.app.experiments import target_invocation

    with api.factory() as uow:
        t, db = uow.tables, uow.connection
        service = Service(uow, api.settings)
        experiment = dict(service.experiment(source.experiment_id))
        eid, mid, fid, jid = (uuid4() for _ in range(4))
        experiment.update(
            id=eid,
            idempotency_key=str(eid),
            config_snapshot=deepcopy(experiment["config_snapshot"]),
        )
        target_row = dict(service.row(t.target_configs, experiment["target_config_id"]))
        target_row.update(
            id=uuid4(), adapter_key="META_SPARK", provider="META", model_id="muse-spark-1.3"
        )
        db.execute(t.target_configs.insert().values(**target_row))
        experiment["target_config_id"] = target_row["id"]
        experiment["config_snapshot"]["target"] = {
            key: target_row[key] for key in experiment["config_snapshot"]["target"]
        }
        experiment["config_snapshot"]["provider_mode"] = "LIVE"
        experiment["config_snapshot"]["provider_dispatch_enabled"] = True
        db.execute(t.experiments.insert().values(**experiment))
        mutation = dict(service.row(t.mutations, source.mutation_id))
        mutation.update(id=mid, experiment_id=eid)
        db.execute(t.mutations.insert().values(**mutation))
        validations = (
            db.execute(
                select(t.mutation_validations)
                .where(t.mutation_validations.c.mutation_id == source.mutation_id)
                .order_by(t.mutation_validations.c.revision)
            )
            .mappings()
            .all()
        )
        latest_report_sha = None
        for row in validations:
            validation = dict(row)
            validation.update(id=uuid4(), mutation_id=mid, previous_validation_id=None)
            if defect == "human_audit":
                evidence = deepcopy(validation["evidence_json"])
                report = ValidationReport.model_validate(evidence["report"])
                report = seal_report(report.model_copy(update={"review_audit": None}))
                body = canonical_json(report.model_dump(mode="json"))
                ref = service.artifacts.put(source.project_id, body, "OTHER", "application/json")
                evidence.update(
                    report=report.model_dump(mode="json"),
                    report_asset_id=str(ref.id),
                    report_asset_sha256=ref.sha256,
                )
                validation["evidence_json"] = evidence
            latest_report_sha = validation["evidence_json"]["report"]["report_sha256"]
            db.execute(t.mutation_validations.insert().values(**validation))
        finding = dict(service.finding(source.finding_id))
        source_job_id = finding["confirmation_job_id"]
        finding.update(
            id=fid,
            experiment_id=eid,
            mutation_id=mid,
            confirmation_job_id=None,
            demo_validation_sha256=latest_report_sha,
        )
        db.execute(t.findings.insert().values(**finding))
        job = dict(service.job(source_job_id))
        if job["result_json"] is None:
            job["result_json"] = null()
        job.update(id=jid, experiment_id=eid, finding_id=fid, idempotency_key=str(jid))
        if defect == "incomplete_job":
            job["state"] = "QUEUED"
        db.execute(t.background_jobs.insert().values(**job))
        canonical_job_id = None if defect == "missing_job" else jid
        db.execute(
            t.findings.update()
            .where(t.findings.c.id == fid)
            .values(confirmation_job_id=canonical_job_id)
        )
        if defect != "observations":
            usage_rows = (
                db.execute(
                    select(t.usage_events).where(
                        t.usage_events.c.experiment_id == source.experiment_id
                    )
                )
                .mappings()
                .all()
            )
            source_observations = (
                db.execute(
                    select(t.model_observations).where(
                        t.model_observations.c.experiment_id == source.experiment_id
                    )
                )
                .mappings()
                .all()
            )
            invocations = {}
            for observation in source_observations:
                image_id = (
                    source.seed_asset_id
                    if observation["phase"] == "SEED_BASELINE"
                    else source.mutated_asset_id
                )
                invocations[observation["operation_key"]] = target_invocation(
                    experiment["config_snapshot"],
                    image_id,
                    service.artifacts.read_verified(image_id),
                )
            keys = {}
            for row in usage_rows:
                # Batch reservations have no response/observation. Clone only
                # the settled calls whose raw evidence this negative fixture
                # retains; each observation must keep its own accounted ticket.
                if row["operation_key"] not in invocations:
                    continue
                usage = dict(row)
                usage["metadata_json"] = {
                    **usage["metadata_json"],
                    "invocation_sha256": fingerprint(invocations[row["operation_key"]]),
                }
                keys[usage["operation_key"]] = "negative-demo-" + uuid4().hex
                usage.update(
                    id=uuid4(),
                    experiment_id=eid,
                    operation_key=keys[usage["operation_key"]],
                    background_job_id=jid if usage["background_job_id"] == source_job_id else None,
                )
                db.execute(t.usage_events.insert().values(**usage))
            sampling = dict(target_row["inference_options"])
            token_key = (
                "max_output_tokens" if "max_output_tokens" in sampling else "max_completion_tokens"
            )
            sampling[token_key] = sampling.get(token_key, 128)
            for row in source_observations:
                observation = dict(row)
                observation.update(
                    id=uuid4(),
                    experiment_id=eid,
                    target_config_id=target_row["id"],
                    invocation_json=invocations[row["operation_key"]],
                    request_fingerprint=fingerprint(invocations[row["operation_key"]]),
                    sampling_config=sampling,
                    mutation_id=mid if row["mutation_id"] else None,
                    operation_key=keys[row["operation_key"]],
                    confirmation_job_id=canonical_job_id if row["confirmation_job_id"] else None,
                )
                db.execute(t.model_observations.insert().values(**observation))
        from services.api.app.exports import eligibility, observations

        current = service.finding(fid)
        from services.api.app.public_demo import _bound_report_sha

        _, report = _bound_report_sha(service, current, mid)
        obs = observations(service, current)
        assert all(item.provenance == "REAL_PROVIDER" for item in obs)
        reasons = eligibility(service, current, report, obs)
        expected = {
            "observations": "COMPLETE_CANONICAL_CONFIRMATION_REQUIRED",
            "human_audit": "EVIDENCE_BOUND_HUMAN_REVIEW_REQUIRED",
            "missing_job": "CANONICAL_JOB_REQUIRED",
            "incomplete_job": "CANONICAL_JOB_INCOMPLETE",
            "raw_evidence": None,
        }[defect]
        if expected:
            assert expected in reasons
        else:
            assert reasons == []
            raw_id = db.scalar(
                select(t.model_observations.c.raw_output_asset_id)
                .where(t.model_observations.c.experiment_id == eid)
                .limit(1)
            )
            raw_asset = service.asset(raw_id)
            (api.root / raw_asset["storage_key"]).write_bytes(b"tampered simulated response")
        if defect != "observations":
            assert len(obs) == 7
        uow.commit()
    return eid


@pytest.mark.parametrize(
    "defect", ["observations", "human_audit", "missing_job", "incomplete_job", "raw_evidence"]
)
async def test_real_provenance_incomplete_evidence_cannot_prepare_or_publish(
    api, vtc_payload, defect
):
    source = await synthesized_run(api, vtc_payload)
    eid = simulated_real_source(api, source, defect)
    response = await api.client.post(f"/api/v1/experiments/{eid}/demo-snapshot")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "REVIEW_REQUIRED"
    with api.factory() as uow:
        assert (
            uow.connection.scalar(
                select(func.count()).select_from(uow.tables.public_demo_snapshots)
            )
            == 0
        )
    assert (await api.client.get("/api/v1/public/demo", headers={"Authorization": ""})).json()[
        "findings"
    ] == []


@pytest.mark.parametrize("pinned_mode", ["DISABLED", None])
async def test_unexecuted_or_unknown_source_is_not_real_provider_provenance(
    api, vtc_payload, pinned_mode
):
    seed, _, vtc = await contract(api, vtc_payload)
    config = await target(api)
    started = await api.client.post(
        "/api/v1/experiments",
        json={
            "project_id": api.project_id,
            "contract_id": vtc["id"],
            "target_config_id": config["id"],
        },
        headers={"Idempotency-Key": "disabled-demo"},
    )
    assert started.status_code == 202, started.text
    eid = started.json()["experiment_id"]
    with api.factory() as uow:
        if pinned_mode is None:
            row = dict(
                uow.connection.execute(
                    select(uow.tables.experiments).where(uow.tables.experiments.c.id == UUID(eid))
                )
                .mappings()
                .one()
            )
            eid = str(uuid4())
            row.update(id=UUID(eid), idempotency_key=eid)
            row["config_snapshot"] = dict(row["config_snapshot"])
            row["config_snapshot"].pop("provider_mode")
            uow.connection.execute(uow.tables.experiments.insert().values(**row))
        uow.connection.execute(
            uow.tables.experiments.update()
            .where(uow.tables.experiments.c.id == UUID(eid))
            .values(state="FAILED")
        )
        uow.commit()
    response = await api.client.post(
        f"/api/v1/experiments/{eid}/demo-snapshot", json={"include_seed_image": True}
    )
    assert response.status_code == 409
    assert "provenance" in response.json()["error"]["message"]
    del seed
