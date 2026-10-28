from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.allow_hosts(["127.0.0.1", "::1", "localhost"])
ROOT = Path(__file__).resolve().parents[2]


def evidence(connection):
    """Small real evidence graph shared by constraint tests."""

    def insert(sql, **values):
        return connection.execute(text(sql + " RETURNING id"), values).scalar_one()

    project = insert("INSERT INTO projects (name) VALUES ('Test project')")
    asset = insert(
        "INSERT INTO artifact_assets (project_id,kind,storage_key,sha256,byte_size,media_type) "
        "VALUES (:p,'SEED_IMAGE','seed.png',:sha,10,'image/png')",
        p=project,
        sha="a" * 64,
    )
    seed = insert(
        "INSERT INTO seed_cases (project_id,image_asset_id,case_key,question,expected_json,"
        "label_provenance,label_reviewer) VALUES (:p,:a,'one','Count?', '{}','HUMAN','owner')",
        p=project,
        a=asset,
    )
    contract = insert(
        "INSERT INTO visual_contracts (seed_id,version,schema_version,contract_json,contract_sha256) "
        "VALUES (:s,1,'1.0.0','{}',:sha)",
        s=seed,
        sha="b" * 64,
    )
    target = insert(
        "INSERT INTO target_configs (project_id,adapter_key,provider,model_id,prompt_template) "
        "VALUES (:p,'LOCAL_FIXTURE','FIXTURE','fixture','Count?')",
        p=project,
    )
    experiment = insert(
        "INSERT INTO experiments (project_id,contract_id,target_config_id,config_snapshot,"
        "max_candidates,max_target_calls,max_estimated_usd,idempotency_key) "
        "VALUES (:p,:c,:t,'{}',20,60,10,'start')",
        p=project,
        c=contract,
        t=target,
    )
    mutation = insert(
        "INSERT INTO mutations (experiment_id,family,strength,generator,recipe_json,state) "
        "VALUES (:e,'brightness_shift',0.1,'DETERMINISTIC','{}','PLANNED')",
        e=experiment,
    )
    finding = insert(
        "INSERT INTO findings (experiment_id,mutation_id,status) VALUES (:e,:m,'SUSPECTED')",
        e=experiment,
        m=mutation,
    )
    return dict(
        project=project,
        asset=asset,
        seed=seed,
        contract=contract,
        target=target,
        experiment=experiment,
        mutation=mutation,
        finding=finding,
    )


def test_fresh_migrations_preserve_baseline_and_map_original_names(migrated_engine):
    from services.api.app.db import load_tables

    assert (ROOT / "services/api/app/db/alembic/0001_initial.sql").read_bytes() == (
        ROOT / "docs/CounterForge_PostgreSQL_Schema_v1.0.sql"
    ).read_bytes()
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0002_workflow_support"
        )
        tables = load_tables(connection)
        names = set(inspect(connection).get_table_names()) - {"alembic_version"}
        assert names == set(tables.metadata.tables)
        assert {"findings", "mutations", "regression_exports", "background_jobs"} <= names
        for table in tables.metadata.tables.values():
            assert {column.name for column in table.columns} == {
                column["name"] for column in inspect(connection).get_columns(table.name)
            }
        assert tables.findings.name == "findings"
        assert tables.mutations.name == "mutations"
        assert tables.regression_exports.name == "regression_exports"


def test_server_uuid_defaults_and_evidence_fks_are_restrict(migrated_engine):
    with migrated_engine.begin() as connection:
        graph = evidence(connection)
        assert all(isinstance(value, UUID) for value in graph.values())
        fks = [
            fk
            for name in inspect(connection).get_table_names()
            for fk in inspect(connection).get_foreign_keys(name)
        ]
        assert fks and all(fk["options"].get("ondelete") == "RESTRICT" for fk in fks)
        for table, key in [
            ("projects", "project"),
            ("artifact_assets", "asset"),
            ("seed_cases", "seed"),
            ("mutations", "mutation"),
        ]:
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text(f"DELETE FROM {table} WHERE id=:id"), {"id": graph[key]})


@pytest.mark.parametrize("subject", ["seed", "mutation"])
def test_observation_attempts_are_unique_per_subject(migrated_engine, subject):
    with migrated_engine.begin() as connection:
        graph = evidence(connection)
        sql = text(
            f"INSERT INTO model_observations (experiment_id,target_config_id,{subject}_id,phase,"
            "attempt_no,request_fingerprint,outcome) VALUES (:e,:t,:s,'RETEST',:n,:f,'CORRECT')"
        )
        values = dict(e=graph["experiment"], t=graph["target"], s=graph[subject], n=1, f="c" * 64)
        connection.execute(sql, values)
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(sql, values)
        connection.execute(sql, {**values, "n": 2})


@pytest.mark.parametrize(
    "table,column,key",
    [
        ("experiments", "state", "experiment"),
        ("mutations", "state", "mutation"),
        ("findings", "status", "finding"),
    ],
)
def test_invalid_evidence_states_are_rejected(migrated_engine, table, column, key):
    with migrated_engine.begin() as connection:
        graph = evidence(connection)
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(f"UPDATE {table} SET {column}='INVALID_STATE' WHERE id=:id"),
                {"id": graph[key]},
            )


def test_background_jobs_constraints_and_nullable_idempotency(migrated_engine):
    with migrated_engine.begin() as connection:
        graph = evidence(connection)
        sql = text(
            "INSERT INTO background_jobs (project_id,kind,request_fingerprint,request_json,"
            "idempotency_key) VALUES (:p,:kind,:f,'{}',:key) RETURNING id,state"
        )
        values = dict(p=graph["project"], kind="EXPORT", f="d" * 64, key="export-one")
        job = connection.execute(sql, values).one()
        assert isinstance(job.id, UUID) and job.state == "QUEUED"
        for changed in [{}, {"f": "e" * 64}, {"kind": "PAID_DISPATCH"}, {"f": "bad"}]:
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(sql, {**values, **changed})
        connection.execute(sql, {**values, "kind": "RETEST"})
        for _ in range(2):
            connection.execute(sql, {**values, "key": None})
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text("UPDATE background_jobs SET state='DISPATCHED' WHERE id=:id"), {"id": job.id}
            )
        assert connection.scalar(text("SELECT count(*) FROM regression_exports")) == 0


def test_shared_uow_commit_rollback_and_job_idempotency(migrated_engine):
    from services.api.app.db import IdempotencyConflict, create_uow_factory

    factory = create_uow_factory(migrated_engine)
    with factory() as uow:
        project = uow.connection.execute(
            uow.tables.projects.insert()
            .values(name="Committed")
            .returning(uow.tables.projects.c.id)
        ).scalar_one()
        job = uow.background_jobs.enqueue(
            project_id=project,
            kind="SEGMENT",
            request_json={"seed_id": "safe-id"},
            idempotency_key="one",
        )
        assert (
            uow.background_jobs.enqueue(
                project_id=project,
                kind="SEGMENT",
                request_json={"seed_id": "safe-id"},
                idempotency_key="one",
            )
            == job
        )
        with pytest.raises(IdempotencyConflict):
            uow.background_jobs.enqueue(
                project_id=project,
                kind="SEGMENT",
                request_json={"seed_id": "different"},
                idempotency_key="one",
            )
        uow.commit()
    with factory() as uow:
        assert uow.background_jobs.get(job)["state"] == "QUEUED"
        uow.connection.execute(uow.tables.projects.insert().values(name="Rolled back"))
    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM projects")) == 1
        assert connection.scalar(text("SELECT count(*) FROM background_jobs")) == 1
    with pytest.raises(RuntimeError), factory() as uow:
        uow.connection.execute(uow.tables.projects.insert().values(name="Exception rollback"))
        raise RuntimeError("abort")
    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM projects")) == 1


def test_concurrent_job_requests_share_one_durable_job(migrated_engine):
    from services.api.app.db import IdempotencyConflict, create_uow_factory

    factory = create_uow_factory(migrated_engine)
    with migrated_engine.begin() as connection:
        project = connection.scalar(
            text("INSERT INTO projects (name) VALUES ('Concurrent') RETURNING id")
        )

    def enqueue(request):
        with factory() as uow:
            job = uow.background_jobs.enqueue(
                project_id=project, kind="EXPORT", request_json=request, idempotency_key="export"
            )
            uow.commit()
            return job

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(enqueue, [{"finding_id": "one"}, {"finding_id": "one"}]))
    assert jobs[0] == jobs[1]
    with pytest.raises(IdempotencyConflict):
        enqueue({"finding_id": "two"})
    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM background_jobs")) == 1


def test_readiness_probes_postgres_without_changing_the_schema(migrated_engine, tmp_path):
    from services.api.app.config import Settings
    from services.api.app.db.readiness import probe_dependencies

    settings = Settings.from_env(
        {"ARTIFACT_ROOT": str(tmp_path), "REDIS_URL": "redis://127.0.0.1:1/0"}
    )
    result = probe_dependencies(settings, migrated_engine)
    assert result.database is True and result.artifacts is True
    assert result.redis is False and result.ready is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("revision", [None, "0001"])
def test_readiness_rejects_missing_or_behind_migrations(database_engine, tmp_path, revision):
    from services.api.app.config import Settings
    from services.api.app.db.migrations import upgrade_database
    from services.api.app.db.readiness import probe_dependencies

    if revision is not None:
        upgrade_database(database_engine, revision)
    settings = Settings.from_env(
        {
            "ARTIFACT_ROOT": str(tmp_path),
            "REDIS_URL": "redis://127.0.0.1:1/0",
        }
    )
    result = probe_dependencies(settings, database_engine)
    assert result.database is False and result.ready is False
