"""The gate that keeps the developer reset endpoints out of production.

These are the tests that must not regress: everything else in devtools.py is
SQL the pure-function suite cannot reach, but whether the router exists is
decidable without a database.

Every setting the gate reads is pinned explicitly in BASE, because Settings
also loads backend/.env and the ambient environment — a developer's local key
must not decide whether these pass.
"""

import pytest
from fastapi import FastAPI

from app.config import Settings
from app.devtools import register_devtools

GOOD_KEY = "k" * 32
BASE = {
    "DATABASE_URL": "postgresql+asyncpg://dvd:dvd@localhost:5432/dvd",
    "CORS_ORIGINS": "http://localhost:5173",
    "ANALYSIS_API_KEY": GOOD_KEY,
    "DEVTOOLS_ENABLED": "false",
    "DEVTOOLS_API_KEY": None,
}


def make_settings(**overrides) -> Settings:
    return Settings(**(BASE | overrides))


def test_env_defaults_to_production():
    # A forgotten ENV must fail closed. Asserted on the field default rather
    # than a constructed instance, which would pick up the ambient ENV.
    assert Settings.model_fields["env"].default == "production"


def test_production_forbids_devtools_at_boot():
    with pytest.raises(ValueError, match="DEVTOOLS_ENABLED"):
        make_settings(ENV="production", DEVTOOLS_ENABLED="true", DEVTOOLS_API_KEY=GOOD_KEY)


def test_production_rejects_wildcard_cors():
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        make_settings(ENV="production", CORS_ORIGINS="*")


def test_production_rejects_short_analysis_key():
    with pytest.raises(ValueError, match="ANALYSIS_API_KEY"):
        make_settings(ENV="production", ANALYSIS_API_KEY="short")


def test_production_allows_an_unset_analysis_key():
    # A blank ANALYSIS_API_KEY parses as "" and means "analysis disabled",
    # which check_admin_key answers with a 503. It must not block boot.
    assert make_settings(ENV="production", ANALYSIS_API_KEY="").is_production
    assert make_settings(ENV="production", ANALYSIS_API_KEY=None).is_production


def test_production_accepts_a_strong_analysis_key():
    settings = make_settings(ENV="production")
    assert settings.is_production
    assert not settings.devtools_available


def test_development_is_not_production():
    assert not make_settings(ENV="development").is_production


def test_devtools_available_in_development():
    settings = make_settings(ENV="development", DEVTOOLS_ENABLED="true", DEVTOOLS_API_KEY=GOOD_KEY)
    assert settings.devtools_available


def test_devtools_available_in_staging():
    settings = make_settings(ENV="staging", DEVTOOLS_ENABLED="true", DEVTOOLS_API_KEY=GOOD_KEY)
    assert settings.devtools_available


def test_devtools_off_when_not_enabled():
    settings = make_settings(ENV="development", DEVTOOLS_API_KEY=GOOD_KEY)
    assert not settings.devtools_available


def test_devtools_off_when_key_missing_or_short():
    # Not an error: the CLI needs no key, so a fresh clone must still boot.
    assert not make_settings(ENV="development", DEVTOOLS_ENABLED="true").devtools_available
    assert not make_settings(
        ENV="development", DEVTOOLS_ENABLED="true", DEVTOOLS_API_KEY="tooshort"
    ).devtools_available


def _dev_paths(settings: Settings) -> list[str]:
    """Dev paths the app actually publishes.

    Read from the OpenAPI schema rather than app.routes: include_router wraps
    its routes, and the schema is the stronger claim anyway — it is what a
    caller can discover.
    """
    app = FastAPI()
    register_devtools(app, settings)
    return sorted(p for p in app.openapi()["paths"] if p.startswith("/api/dev"))


def test_router_not_registered_in_production():
    assert _dev_paths(make_settings(ENV="production")) == []


def test_router_not_registered_without_a_usable_key():
    assert _dev_paths(make_settings(ENV="development", DEVTOOLS_ENABLED="true")) == []


def test_router_registered_in_development():
    settings = make_settings(ENV="development", DEVTOOLS_ENABLED="true", DEVTOOLS_API_KEY=GOOD_KEY)
    assert _dev_paths(settings) == ["/api/dev/ais", "/api/dev/pu", "/api/dev/scenes"]


def _ops_paths(settings: Settings) -> list[str]:
    from app.main import register_ops

    app = FastAPI()
    register_ops(app, settings)
    return sorted(app.openapi()["paths"])


def test_production_publishes_no_pu_spending_route():
    """POST /api/analysis/{roi} must not exist in production.

    It bypasses PU_MONTHLY_CEILING, and a scene that failed *after* its pixel
    fetch is retried on every call — each retry a fresh spend. The scheduler is
    protected from that by scene_has_pu_spend; this path never was. Forcing a
    run in production is scripts/analyze.py over a shell instead.
    """
    assert _ops_paths(make_settings(ENV="production")) == []


def test_analysis_trigger_available_outside_production():
    assert _ops_paths(make_settings(ENV="development")) == ["/api/analysis/{roi}"]
    assert _ops_paths(make_settings(ENV="staging")) == ["/api/analysis/{roi}"]


def test_register_devtools_reports_whether_it_registered():
    prod = make_settings(ENV="production")
    dev = make_settings(ENV="development", DEVTOOLS_ENABLED="true", DEVTOOLS_API_KEY=GOOD_KEY)
    assert register_devtools(FastAPI(), prod) is False
    assert register_devtools(FastAPI(), dev) is True
