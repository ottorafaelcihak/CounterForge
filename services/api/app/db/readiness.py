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


@dataclass(frozen=True)
class Readiness:
    database: bool
    redis: bool
    artifacts: bool

    @property
    def ready(self) -> bool:
        return self.database and self.redis and self.artifacts


def probe_dependencies(settings: Settings, engine: Engine | None = None) -> Readiness:
    """Return booleans only: never expose exception text, paths or service URLs."""
    database = redis = artifacts = False
    owned_engine = engine is None
    try:
        engine = engine or create_database_engine(settings)
        with engine.connect() as connection:
            expected = set(ScriptDirectory.from_config(migration_config()).get_heads())
            installed = set(MigrationContext.configure(connection).get_current_heads())
            database = connection.scalar(text("SELECT 1")) == 1 and installed == expected
    except Exception:
        pass
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
            redis = bool(client.ping())
    except Exception:
        pass
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
    return Readiness(database=database, redis=redis, artifacts=artifacts)
