from decimal import InvalidOperation

import pytest

from scripts.smoke_meta import smoke_inputs
from services.api.app.config import Settings


def test_provider_configuration_defaults_disabled_and_hides_model_key():
    settings = Settings.from_env({"MODEL_API_KEY": "fake-test-key"})
    assert settings.provider_mode == "DISABLED" and not settings.enqueue_jobs
    assert "fake-test-key" not in repr(settings) + settings.model_dump_json()


@pytest.mark.parametrize(
    "env",
    [
        {"PROVIDER_MODE": "FIXTURE"},
        {"PROVIDER_MODE": "FIXTURE", "ALLOW_SYNTHETIC_FIXTURES": "1", "APP_ENV": "production"},
        {"PROVIDER_MODE": "LIVE", "RUN_META_LIVE_TESTS": "1"},
        {"PROVIDER_MODE": "LIVE", "MODEL_API_KEY": "fake-test-key"},
        {"MAX_CONCURRENT_PROVIDER_CALLS": "2"},
    ],
)
def test_unsafe_provider_modes_fail_closed(env):
    with pytest.raises(ValueError):
        Settings.from_env(env)


def test_fixture_and_live_authorization_are_explicit():
    assert (
        Settings.from_env(
            {"PROVIDER_MODE": "FIXTURE", "ALLOW_SYNTHETIC_FIXTURES": "1", "APP_ENV": "test"}
        ).provider_mode
        == "FIXTURE"
    )
    assert (
        Settings.from_env(
            {"PROVIDER_MODE": "LIVE", "RUN_META_LIVE_TESTS": "1", "MODEL_API_KEY": "fake-test-key"}
        ).provider_mode
        == "LIVE"
    )


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"RUN_META_LIVE_TESTS": "1"},
        {"RUN_META_LIVE_TESTS": "1", "PROVIDER_MODE": "LIVE"},
        {"RUN_META_LIVE_TESTS": "1", "PROVIDER_MODE": "LIVE", "SMOKE_META_MAX_USD": "NaN"},
        {"RUN_META_LIVE_TESTS": "1", "PROVIDER_MODE": "LIVE", "SMOKE_META_MAX_USD": "Infinity"},
    ],
)
def test_smoke_requires_explicit_finite_limits_and_references_before_any_io(env):
    with pytest.raises((ValueError, KeyError, InvalidOperation)):
        smoke_inputs(env)
