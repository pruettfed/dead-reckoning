"""Pytest config: load .env so app.config.Settings can construct in unit tests.

Unit tests don't actually need DATABASE_URL or CORS_ORIGINS, but Settings is
required-by-default. Provide harmless dummies if .env is missing.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://dvd:dvd@localhost:5432/dvd")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:5173")
# ENV now defaults to production, whose invariants would reject this dummy
# config. Tests run as development; the prod rules are exercised explicitly in
# test_devtools_gate.py by constructing Settings directly.
os.environ.setdefault("ENV", "development")
