"""The version string is the single source of truth for every service.

The frontend never holds a version of its own — it renders whatever
/api/health reports — so these assertions are the only thing standing
between a typo'd bump and a wrong number on screen.
"""

import re

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import Health
from app.version import VERSION

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_version_is_major_minor_patch():
    # Catches the two bumps that look right and aren't: a "v" prefix (the
    # branch name, not the version) and a two-part "1.1" (no patch slot).
    assert SEMVER.match(VERSION), f"VERSION must be MAJOR.MINOR.PATCH, got {VERSION!r}"


def test_openapi_version_matches():
    assert app.version == VERSION


def test_health_response_model_declares_version():
    # The API publishes only what its response model declares, so a field
    # missing here is a field the frontend can never read.
    assert "version" in Health.model_fields


def test_health_serves_version_without_a_database():
    # The wire format, not just the model. No lifespan (TestClient is not used
    # as a context manager) and no Postgres: health degrades to database=error
    # and must still report the version, because "which version is deployed?"
    # has to be answerable precisely when the instance is unhealthy.
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["version"] == VERSION
