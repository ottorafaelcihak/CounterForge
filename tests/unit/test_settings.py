from pathlib import Path

import pytest

from services.api.app.config import Settings


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://user:secret@example.com",
        "https://example.com/path",
        "https://example.com?token=secret",
        "file:///tmp/site",
    ],
)
def test_cors_configuration_requires_one_http_origin(origin):
    with pytest.raises(ValueError):
        Settings.from_env({"WEB_ORIGIN": origin})


def test_settings_build_database_url_from_explicit_components_without_losing_password():
    settings = Settings.from_env(
        {
            "PGHOST": "127.0.0.1",
            "PGPORT": "55432",
            "PGDATABASE": "test",
            "PGUSER": "tester",
            "PGPASSWORD": "p@ss:/?#%word",
            "ARTIFACT_ROOT": "/tmp/assets",
        }
    )
    assert settings.database_url.host == "127.0.0.1"
    assert settings.database_url.port == 55432
    assert settings.database_url.password == "p@ss:/?#%word"
    assert settings.artifact_root == Path("/tmp/assets")
    assert "p@ss" not in repr(settings)


def test_fully_expanded_url_takes_precedence_and_secrets_are_hidden():
    settings = Settings.from_env(
        {
            "DATABASE_URL": "postgresql+psycopg://test:private@localhost/db",
            "PGPASSWORD": "ignored",
            "REDIS_URL": "redis://:redis-secret@localhost/2",
        }
    )
    assert settings.database_url.database == "db"
    assert settings.redis_url.get_secret_value().endswith("localhost/2")
    assert "private" not in repr(settings) and "redis-secret" not in repr(settings)
    assert "private" not in settings.model_dump_json()


@pytest.mark.parametrize(
    "env",
    [
        {"DATABASE_URL": "postgresql+psycopg://user:${POSTGRES_PASSWORD}@localhost/db"},
        {"PGHOST": "${PGHOST}"},
        {"DATABASE_URL": "sqlite:///prod.db"},
        {"DATABASE_URL": "not-a-url-secret"},
        {"PGPORT": "bad-secret"},
    ],
)
def test_invalid_or_unexpanded_database_configuration_fails_without_echoing_values(env):
    with pytest.raises(ValueError) as caught:
        Settings.from_env(env)
    assert "bad-secret" not in str(caught.value)
    assert "not-a-url-secret" not in str(caught.value)


def test_missing_database_configuration_never_falls_back_to_a_database():
    settings = Settings.from_env({})
    with pytest.raises(ValueError, match="database configuration"):
        _ = settings.database_url


def test_explicit_password_components_are_literal_not_interpolated():
    settings = Settings.from_env(
        {
            "PGHOST": "localhost",
            "PGUSER": "test",
            "PGDATABASE": "test",
            "PGPASSWORD": "literal${not_an_env_reference}$secret",
        }
    )
    assert settings.database_url.password == "literal${not_an_env_reference}$secret"


@pytest.mark.filterwarnings("ignore:A test tried to use socket.getaddrinfo:UserWarning")
def test_readiness_performs_real_artifact_probe_and_reports_unavailable_services(tmp_path):
    from services.api.app.db.readiness import probe_dependencies

    settings = Settings.from_env(
        {"ARTIFACT_ROOT": str(tmp_path), "REDIS_URL": "redis://127.0.0.1:1/0"}
    )
    result = probe_dependencies(settings)
    assert result.artifacts is True
    assert result.database is False and result.redis is False and result.ready is False
    assert list(tmp_path.iterdir()) == []
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    assert probe_dependencies(Settings.from_env({"ARTIFACT_ROOT": str(blocked)})).artifacts is False
