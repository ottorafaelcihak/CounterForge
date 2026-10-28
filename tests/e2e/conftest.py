"""Opt-in real-process setup verification, isolated from application databases."""

import os
import signal
import subprocess
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from secrets import token_hex
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from redis import Redis
from rq import Queue
from sqlalchemy import text

from services.api.app.accounting import Budget
from services.api.app.config import Settings
from services.api.app.db import create_uow_factory
from tests.integrations import conftest as integration_fixtures

database_engine = integration_fixtures.database_engine
migrated_engine = integration_fixtures.migrated_engine

ROOT = Path(__file__).resolve().parents[2]


def free_port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_http(url, process):
    import urllib.request

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail("An owned test service exited during startup; inspect its task-local log.")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, ValueError):
            time.sleep(0.15)
    pytest.fail("An owned test service did not become ready within 60 seconds.")


@pytest.fixture
def setup_stack(migrated_engine, tmp_path):
    if os.environ.get("RUN_COUNTERFORGE_E2E") != "1":
        pytest.skip("set RUN_COUNTERFORGE_E2E=1 with explicit disposable PostgreSQL/Redis URLs")
    raw_redis = os.environ.get("COUNTERFORGE_TEST_REDIS_URL", "")
    parsed = urlsplit(raw_redis)
    if (
        parsed.scheme != "redis"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.path != "/15"
    ):
        pytest.fail("COUNTERFORGE_TEST_REDIS_URL must be an explicit loopback Redis database /15")
    if not (ROOT / "apps/web/.next/BUILD_ID").is_file():
        pytest.fail("Build the web app before running the browser test (make build-web)")
    with migrated_engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    database_url = migrated_engine.url.update_query_dict(
        {"options": f"-csearch_path={schema},public"}
    ).render_as_string(hide_password=False)
    owner = token_hex(32)
    queue_name = "cf_e2e_" + uuid4().hex
    api_port, web_port = free_port(), free_port()
    origin = f"http://127.0.0.1:{web_port}"
    env = {
        "PATH": os.environ["PATH"],
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(ROOT),
        "PYTHONUNBUFFERED": "1",
        "NEXT_TELEMETRY_DISABLED": "1",
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "REDIS_URL": raw_redis,
        "OWNER_AUTH_TOKEN": owner,
        "OWNER_LABEL": "e2e-reviewer",
        "ARTIFACT_ROOT": str(tmp_path / "artifacts"),
        "WEB_ORIGIN": origin,
        "API_INTERNAL_URL": f"http://127.0.0.1:{api_port}",
        "PROVIDER_MODE": "FIXTURE",
        "ALLOW_SYNTHETIC_FIXTURES": "1",
        "MODEL_API_KEY": "",
        "RUN_META_LIVE_TESTS": "0",
        "ENQUEUE_JOBS": "1",
        "QUEUE_NAME": queue_name,
        "PRICING_PATH": str(ROOT / "pricing.toml"),
    }
    settings = Settings.from_env(env)
    factory = create_uow_factory(migrated_engine)
    with factory() as uow:
        Budget(uow).configure_deployment_allowance(
            max_usd=Decimal(0),
            max_calls=100,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            configured_by="isolated-browser-fixture",
        )
        uow.commit()
    processes, logs = [], []

    def start(name, argv, cwd=ROOT):
        log = (tmp_path / (name + ".log")).open("w")
        logs.append(log)
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        processes.append(process)
        return process

    try:
        api_process = start(
            "api",
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "uvicorn",
                "services.api.app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--no-access-log",
            ],
        )
        wait_http(f"http://127.0.0.1:{api_port}/api/v1/health/live", api_process)
        start("worker", [str(ROOT / ".venv/bin/python"), "-m", "services.worker.app.main"])
        web_process = start(
            "web",
            [
                str(ROOT / "apps/web/node_modules/.bin/next"),
                "start",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(web_port),
            ],
            ROOT / "apps/web",
        )
        wait_http(origin + "/api/session", web_process)
        yield SimpleNamespace(
            origin=origin, owner=owner, factory=factory, settings=settings, tmp_path=tmp_path
        )
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
        for log in logs:
            log.close()
        with Redis.from_url(raw_redis, socket_connect_timeout=1, socket_timeout=2) as connection:
            Queue(queue_name, connection=connection).delete(delete_jobs=True)
