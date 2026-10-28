"""Opt-in PostgreSQL tests: never use application/production database settings."""

import ipaddress
import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import NullPool


@pytest.fixture
def database_engine() -> Iterator[Engine]:
    raw_url = os.environ.get("COUNTERFORGE_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip(
            "set COUNTERFORGE_TEST_DATABASE_URL to a disposable loopback PostgreSQL database"
        )
    url = make_url(raw_url)
    if url.drivername != "postgresql+psycopg" or not url.host:
        pytest.fail("test database must use postgresql+psycopg and an explicit loopback address")
    try:
        loopback = ipaddress.ip_address(url.host).is_loopback
    except ValueError:
        loopback = url.host == "localhost"
    if not loopback:
        pytest.fail("test database host must be loopback")

    schema = "cf_test_" + uuid4().hex
    admin = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 3})
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url,
        poolclass=NullPool,
        connect_args={"connect_timeout": 3, "options": f"-csearch_path={schema},public"},
    )
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def migrated_engine(database_engine: Engine) -> Engine:
    from services.api.app.db.migrations import upgrade_database

    upgrade_database(database_engine)
    return database_engine


@pytest.fixture
async def api(migrated_engine, tmp_path):
    from types import SimpleNamespace

    import httpx2
    from pydantic import SecretStr

    from services.api.app.config import Settings
    from services.api.app.db import create_uow_factory
    from services.api.app.main import create_app

    settings = Settings(
        artifact_root=tmp_path,
        owner_auth_token=SecretStr("phase02-test-owner-token"),
        web_origin="http://localhost:3000",
    )
    application = create_app(settings=settings, engine=migrated_engine)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=application),
        base_url="http://test",
        headers={"Authorization": "Bearer phase02-test-owner-token"},
    ) as client:
        project = await client.post("/api/v1/projects", json={"name": "Inventory research"})
        assert project.status_code == 201, project.text
        yield SimpleNamespace(
            client=client,
            settings=settings,
            factory=create_uow_factory(migrated_engine),
            project_id=project.json()["id"],
            root=tmp_path,
        )
