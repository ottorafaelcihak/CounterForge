import os
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from counterforge_contracts.base import ContractModel
from counterforge_contracts.vtc import Limits
from pydantic import Field, PrivateAttr, SecretStr, field_validator
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


def validate_origin(value: str) -> str:
    origin = urlsplit(value)
    if (
        origin.scheme not in {"http", "https"}
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.path
        or origin.query
        or origin.fragment
        or "*" in value
        or value != value.strip()
    ):
        raise ValueError("WEB_ORIGIN must be one HTTP or HTTPS origin")
    if origin.port is not None and not 1 <= origin.port <= 65535:
        raise ValueError("WEB_ORIGIN has an invalid port")
    return value


class Settings(ContractModel):
    """Explicit environment loading; never reads dotenv files or expands variables.

    Database credentials stay private and are excluded from model dumps/repr.
    Constructing settings performs no network or filesystem operations.
    """

    app_env: str = "development"
    api_bind: str = "127.0.0.1"
    api_port: int = 8000
    run_meta_live_tests: bool = False
    max_concurrent_provider_calls: int = 1
    default_limits: Limits = Limits()
    artifact_root: Path = Path("/data/artifacts")
    owner_auth_token: SecretStr = Field(default=SecretStr(""), exclude=True, repr=False)
    owner_label: str = Field(default="local-owner", min_length=1, max_length=100)
    web_origin: str = "http://localhost:3000"
    redis_url: SecretStr = Field(
        default=SecretStr("redis://127.0.0.1:6379/0"), exclude=True, repr=False
    )
    _database_url: URL | None = PrivateAttr(default=None)

    _valid_origin = field_validator("web_origin")(validate_origin)

    @property
    def database_url(self) -> URL:
        if self._database_url is None:
            raise ValueError("Explicit database configuration is required")
        return self._database_url

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        keys = (
            "DATABASE_URL",
            "PGHOST",
            "PGPORT",
            "PGDATABASE",
            "PGUSER",
            "PGPASSWORD",
            "REDIS_URL",
        )
        # Component passwords are literal data, even when they contain '$'.
        if any(
            re.search(r"\$(?:\{|[A-Za-z_])", env.get(key, ""))
            for key in keys
            if key != "PGPASSWORD"
        ):
            raise ValueError("Service configuration must be fully expanded")
        try:
            database_url = None
            if env.get("DATABASE_URL"):
                database_url = make_url(env["DATABASE_URL"])
                if database_url.drivername in {"postgres", "postgresql"}:
                    database_url = database_url.set(drivername="postgresql+psycopg")
                if database_url.drivername != "postgresql+psycopg":
                    raise ValueError
                if not all((database_url.host, database_url.username, database_url.database)):
                    raise ValueError
                # Accessing port validates it without opening a connection.
                if database_url.port is not None and not 1 <= database_url.port <= 65535:
                    raise ValueError
            elif any(env.get(key) for key in keys[1:6]):
                port = int(env.get("PGPORT", "5432"))
                if not 1 <= port <= 65535 or not all(
                    env.get(key) for key in ("PGHOST", "PGUSER", "PGDATABASE")
                ):
                    raise ValueError
                database_url = URL.create(
                    "postgresql+psycopg",
                    host=env["PGHOST"],
                    port=port,
                    username=env["PGUSER"],
                    password=env.get("PGPASSWORD"),
                    database=env["PGDATABASE"],
                )
            settings = cls(
                app_env=env.get("APP_ENV", "development"),
                api_bind=env.get("API_BIND", "127.0.0.1"),
                api_port=int(env.get("API_PORT", "8000")),
                run_meta_live_tests=env.get("RUN_META_LIVE_TESTS", "0") == "1",
                max_concurrent_provider_calls=int(env.get("MAX_CONCURRENT_PROVIDER_CALLS", "1")),
                artifact_root=Path(env.get("ARTIFACT_ROOT", "/data/artifacts")),
                owner_auth_token=SecretStr(env.get("OWNER_AUTH_TOKEN", "")),
                owner_label=env.get("OWNER_LABEL", "local-owner"),
                web_origin=env.get("WEB_ORIGIN", "http://localhost:3000"),
                redis_url=SecretStr(env.get("REDIS_URL", "redis://127.0.0.1:6379/0")),
            )
        except (ValueError, TypeError, ArgumentError):
            raise ValueError(
                "Invalid service configuration; check explicit environment components"
            ) from None
        settings._database_url = database_url
        return settings
