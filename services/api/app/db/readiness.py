"""Free dependency probes, ready for later owner-authenticated route integration."""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from redis import Redis
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..config import Settings
from .migrations import migration_config
from .uow import create_database_engine

WORKER_HEARTBEAT_TTL_SECONDS = 15


def worker_heartbeat_key(queue_name: str) -> str:
    """Exact queue-specific heartbeat key; carries no credentials or payload."""
    return f"counterforge:worker-heartbeat:{queue_name}"


@dataclass(frozen=True)
class Readiness:
    database: bool
    migrations: bool
    redis: bool
    artifacts: bool
    worker_heartbeat: bool

    @property
    def ready(self) -> bool:
        return (
            self.database
            and self.migrations
            and self.redis
            and self.artifacts
            and self.worker_heartbeat
        )


def probe_dependencies(settings: Settings, engine: Engine | None = None) -> Readiness:
    """Return booleans only: never expose exception text, paths or service URLs.

    No provider connectivity or inference is probed here. Worker heartbeat is
    fresh only while its short-lived Redis key exists (see worker main).
    """
    database = migrations = redis_ok = artifacts = worker_heartbeat = False
    owned_engine = engine is None
    try:
        engine = engine or create_database_engine(settings)
        with engine.connect() as connection:
            database = connection.scalar(text("SELECT 1")) == 1
            if database:
                expected = set(ScriptDirectory.from_config(migration_config()).get_heads())
                installed = set(MigrationContext.configure(connection).get_current_heads())
                migrations = installed == expected
    except Exception:
        database = migrations = False
    finally:
        if owned_engine and engine is not None:
            engine.dispose()
    try:
        with Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=False,
        ) as client:
            redis_ok = bool(client.ping())
            if redis_ok:
                worker_heartbeat = bool(client.exists(worker_heartbeat_key(settings.queue_name)))
    except Exception:
        redis_ok = worker_heartbeat = False
    temporary_path: Path | None = None
    try:
        # Probe actual write/fsync/read/delete; os.access alone is insufficient.
        with tempfile.NamedTemporaryFile(
            dir=settings.artifact_root, prefix=".ready-", delete=False
        ) as file:
            temporary_path = Path(file.name)
            file.write(b"counterforge-ready")
            file.flush()
            os.fsync(file.fileno())
        artifacts = temporary_path.read_bytes() == b"counterforge-ready"
        temporary_path.unlink()
        temporary_path = None
    except OSError:
        artifacts = False
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
    return Readiness(
        database=database,
        migrations=migrations,
        redis=redis_ok,
        artifacts=artifacts,
        worker_heartbeat=worker_heartbeat,
    )
