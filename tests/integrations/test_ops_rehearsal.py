"""Gated disposable operational rehearsal (root-run; see scripts/rehearse_ops.sh).

Against a dedicated disposable PostgreSQL (public schema), rehearsal Redis
DB /15 and host artifact roots this covers: fixture-run persistence,
durable queued export intent surviving a Redis reset confined to the
rehearsal DB/queue, dispatched-work review gates, backup/restore into an
empty DB + artifact dir, hash/count comparison and known-bundle
verification. Skipped unless RUN_COUNTERFORGE_OPS_REHEARSAL=1 with
explicit disposable loopback URLs and pg tool executables on PATH or in
OPS_PG_DUMP/OPS_PG_RESTORE. No provider calls: fixture mode only.
"""

import hashlib
import os
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"])

GATE = os.environ.get("RUN_COUNTERFORGE_OPS_REHEARSAL") == "1"
REDIS_URL = os.environ.get("COUNTERFORGE_TEST_REDIS_URL", "")
DB_URL = os.environ.get("COUNTERFORGE_TEST_DATABASE_URL", "")
RESTORED_URL = os.environ.get("COUNTERFORGE_OPS_RESTORED_DATABASE_URL", "")


def _loopback(host: str | None) -> bool:
    return host in {"127.0.0.1", "::1", "localhost"}


def require_gate() -> None:
    if not GATE:
        pytest.skip("set RUN_COUNTERFORGE_OPS_REHEARSAL=1 (see scripts/rehearse_ops.sh)")
    # Fail (never skip, never flush) when the gate is on but URLs are not
    # provably the dedicated rehearsal instances.
    from urllib.parse import urlsplit

    redis = urlsplit(REDIS_URL)
    if (
        redis.scheme != "redis"
        or not _loopback(redis.hostname)
        or redis.path != "/15"
        or redis.query
        or redis.fragment
    ):
        pytest.fail("COUNTERFORGE_TEST_REDIS_URL must be loopback redis with exact path /15")
    databases = {}
    for label, raw, expected in (
        ("COUNTERFORGE_TEST_DATABASE_URL", DB_URL, "counterforge_ops_test"),
        ("COUNTERFORGE_OPS_RESTORED_DATABASE_URL", RESTORED_URL, "counterforge_ops_restored"),
    ):
        parts = urlsplit(raw)
        name = parts.path.lstrip("/")
        if parts.scheme != "postgresql+psycopg" or not _loopback(parts.hostname) or not name:
            pytest.fail(f"{label} must be a loopback postgresql+psycopg URL")
        if name != expected:
            pytest.fail(f"{label} must name the dedicated {expected} database")
        databases[label] = name
    if len(set(databases.values())) != 2:
        pytest.fail("source and restored rehearsal databases must be distinct")


async def make_api(tmp_path):
    """Mirror integrations' api fixture, but on the rehearsal public schema."""
    import httpx2
    from pydantic import SecretStr

    from services.api.app.config import Settings
    from services.api.app.db import create_uow_factory
    from services.api.app.db.migrations import upgrade_database
    from services.api.app.main import create_app

    engine = create_engine(make_url(DB_URL), poolclass=NullPool)
    upgrade_database(engine)
    # Enqueues must target the validated rehearsal Redis (loopback /15 on
    # the task-owned port), never the default 127.0.0.1:6379/0. Set both
    # explicitly before any enqueue can happen.
    settings = Settings(
        artifact_root=tmp_path / "artifacts",
        owner_auth_token=SecretStr("phase09-ops-test-owner"),
        web_origin="http://localhost:3000",
        redis_url=SecretStr(REDIS_URL),
        queue_name="counterforge-ops-rehearsal",
    )
    settings._database_url = make_url(DB_URL)
    app = create_app(settings=settings, engine=engine)
    client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer phase09-ops-test-owner"},
    )
    project = await client.post("/api/v1/projects", json={"name": "Ops rehearsal"})
    assert project.status_code == 201, project.text
    api = SimpleNamespace(
        client=client,
        settings=settings,
        factory=create_uow_factory(engine),
        project_id=project.json()["id"],
        root=tmp_path / "artifacts",
        engine=engine,
    )
    return api


async def test_rehearsal_backup_restore_and_redis_recovery(vtc_payload, monkeypatch, tmp_path):
    require_gate()
    from counterforge_regression.bundle import verify_bundle
    from redis import Redis

    from scripts.backup_restore import do_backup, do_restore, validate_backup
    from scripts.verify_export import fixture_bundle
    from services.api.app.artifacts import BlobRef, BlobStore
    from services.api.app.db import create_database_engine, create_uow_factory
    from services.api.app.service import Service
    from services.worker.app.experiments import execute_experiment
    from services.worker.app.jobs import recover_jobs
    from services.worker.app.queue import enqueue_ids
    from tests.integrations.test_experiment_pipeline import (
        fixture_settings,
        replies,
        valid_candidate,
    )

    api = await make_api(tmp_path)
    try:
        api.settings.provider_mode = "FIXTURE"
        api.settings.allow_synthetic_fixtures = True
        settings = fixture_settings(api).model_copy(
            update={"queue_name": "counterforge-ops-rehearsal"}
        )
        eid, _mutation = await valid_candidate(api, vtc_payload)
        with api.factory() as uow:
            uow.connection.execute(
                uow.tables.experiments.update()
                .where(uow.tables.experiments.c.id == UUID(eid))
                .values(state="BASELINE_VALIDATING")
            )
            uow.commit()
        replies(monkeypatch, [1, 1, 1])
        for _ in range(3):
            await execute_experiment(api.factory, settings, UUID(eid))

        assert settings.redis_url.get_secret_value() == REDIS_URL
        assert settings.queue_name == "counterforge-ops-rehearsal"
        bundle_bytes = fixture_bundle()
        bundle_sha = hashlib.sha256(bundle_bytes).hexdigest()
        with api.factory() as uow:
            service = Service(uow, settings)
            # Registered fixture asset (an artifact_assets row, not an orphan
            # blob) plus an explicit durable pending export intent. No worker
            # runs in this test and the export is never claimed/dispatched,
            # so the intent must stay QUEUED with no paid/fake calls.
            bundle_ref = service.artifacts.put(
                UUID(api.project_id), bundle_bytes, "REGRESSION_ZIP", "application/zip"
            )
            alias = service.artifacts.put(
                UUID(api.project_id), bundle_bytes, "OTHER", "application/zip"
            )
            assert alias.id != bundle_ref.id and alias.storage_key == bundle_ref.storage_key
            export_job = uow.background_jobs.enqueue(
                project_id=UUID(api.project_id),
                kind="EXPORT",
                request_json={"finding_id": str(bundle_ref.id)},
                idempotency_key="ops-rehearsal-export",
            )
            uow.commit()
            counts_before = {
                name: uow.connection.scalar(select(func.count()).select_from(table)) or 0
                for name, table in (
                    ("usage_events", uow.tables.usage_events),
                    ("experiment_events", uow.tables.experiment_events),
                    ("background_jobs", uow.tables.background_jobs),
                    ("artifact_assets", uow.tables.artifact_assets),
                )
            }
            asset_row = (
                uow.connection.execute(
                    select(uow.tables.artifact_assets).where(
                        uow.tables.artifact_assets.c.id == bundle_ref.id
                    )
                )
                .mappings()
                .one()
            )
            job_row = (
                uow.connection.execute(
                    select(uow.tables.background_jobs).where(
                        uow.tables.background_jobs.c.id == export_job
                    )
                )
                .mappings()
                .one()
            )
        assert counts_before["usage_events"] >= 3 and counts_before["background_jobs"] >= 1
        assert counts_before["experiment_events"] >= 1 and counts_before["artifact_assets"] >= 1
        assert asset_row["project_id"] == UUID(api.project_id)
        assert asset_row["kind"] == "REGRESSION_ZIP" and asset_row["sha256"] == bundle_sha
        assert job_row["kind"] == "EXPORT" and job_row["state"] == "QUEUED"

        # Redis loss confined to rehearsal DB /15 and its exact queue.
        redis = Redis.from_url(REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        assert redis.connection_pool.connection_kwargs.get("db") == 15
        try:
            redis.flushdb()  # dedicated rehearsal Redis instance + DB /15 only
            assert enqueue_ids(settings, [export_job]) == 1
            redis.flushdb()
            assert export_job in recover_jobs(api.factory, settings)
            assert enqueue_ids(settings, [export_job]) == 1
        finally:
            redis.flushdb()
            redis.close()
        # Redis loss/recovery must not dispatch or drop the durable intent:
        # still QUEUED in PostgreSQL, never claimed by any worker here.
        with api.factory() as uow:
            state = uow.connection.scalar(
                select(uow.tables.background_jobs.c.state).where(
                    uow.tables.background_jobs.c.id == export_job
                )
            )
        assert state == "QUEUED"

        backup_dir = do_backup(settings, tmp_path / "backups", os.environ.get("OPS_PG_DUMP"))
        manifest = validate_backup(backup_dir)
        assert manifest["table_counts"]["background_jobs"] >= 1
        assert manifest["table_counts"]["artifact_assets"] > manifest["artifact_count"]
        assert (
            sum(
                entry["path"] == "artifacts/" + bundle_ref.storage_key
                for entry in manifest["files"]
            )
            == 1
        )

        restored_artifacts = tmp_path / "restored-artifacts"
        restored = SimpleNamespace(
            database_url=make_url(RESTORED_URL), artifact_root=restored_artifacts
        )
        result = do_restore(
            restored,
            backup_dir,
            restored_artifacts,
            os.environ.get("OPS_PG_RESTORE"),
            confirm_disposable=True,
        )
        assert result["restored_artifacts"] == manifest["artifact_count"]
        for name, expected in manifest["table_counts"].items():
            assert result["table_counts"][name] == expected, name

        engine = create_database_engine(restored)
        try:
            with create_uow_factory(engine)() as uow:
                row = (
                    uow.connection.execute(
                        select(uow.tables.artifact_assets).where(
                            uow.tables.artifact_assets.c.id == bundle_ref.id
                        )
                    )
                    .mappings()
                    .one()
                )
                # The same registered asset row and the pending export intent
                # survive backup/restore; usage/experiment event counts are
                # already compared against the manifest for every table above.
                assert (
                    uow.connection.scalar(
                        select(uow.tables.artifact_assets.c.storage_key).where(
                            uow.tables.artifact_assets.c.id == alias.id
                        )
                    )
                    == bundle_ref.storage_key
                )
                assert row["id"] == bundle_ref.id
                assert row["project_id"] == UUID(api.project_id)
                assert row["kind"] == "REGRESSION_ZIP"
                restored_job = (
                    uow.connection.execute(
                        select(uow.tables.background_jobs).where(
                            uow.tables.background_jobs.c.id == export_job
                        )
                    )
                    .mappings()
                    .one()
                )
                assert restored_job["kind"] == "EXPORT"
                assert restored_job["state"] == "QUEUED"
            data = BlobStore(restored_artifacts).read_verified(
                BlobRef(
                    row["project_id"],
                    row["storage_key"],
                    row["sha256"],
                    row["byte_size"],
                    row["media_type"],
                )
            )
            assert hashlib.sha256(data).hexdigest() == bundle_sha
            verify_bundle(data)
        finally:
            engine.dispose()
    finally:
        await api.client.aclose()
        api.engine.dispose()


async def test_rehearsal_dispatched_work_stays_in_review_after_redis_loss(tmp_path):
    require_gate()
    from decimal import Decimal

    from counterforge_meta.usage import CallIntent

    from services.api.app.provider_accounting import Accounting
    from services.api.app.service import Service
    from services.worker.app.jobs import claim_job, recover_jobs
    from tests.integrations.test_provider_jobs import queued

    api = await make_api(tmp_path)
    try:
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
            CallIntent("sam-3.1", "SAM", "b" * 64, "rehearsal", Decimal("0.1"), 1)
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
        assert job_id not in recover_jobs(api.factory, settings)
        with api.factory() as uow:
            assert Service(uow, settings).job(job_id)["state"] == "REVIEW_REQUIRED"
            row = (
                uow.connection.execute(
                    select(uow.tables.usage_events).where(
                        uow.tables.usage_events.c.background_job_id == job_id
                    )
                )
                .mappings()
                .one()
            )
            assert row["status"] == "UNKNOWN_CHARGE"
    finally:
        await api.client.aclose()
        api.engine.dispose()
