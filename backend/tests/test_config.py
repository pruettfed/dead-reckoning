"""Whitespace stripped from settings values.

Hit in prod: a value pasted from a fenced code block into Railway's variable
UI carried a trailing newline into DATABASE_URL, and asyncpg went looking for
a database literally named "dvd\\n". Nothing here should ever regress that.
"""

from app.config import Settings

BASE = {
    "DATABASE_URL": "postgresql+asyncpg://dvd:dvd@localhost:5432/dvd",
    "CORS_ORIGINS": "http://localhost:5173",
}


def test_trailing_newline_on_database_url_is_stripped():
    s = Settings(**(BASE | {"DATABASE_URL": "postgresql+asyncpg://dvd:dvd@localhost:5432/dvd\n"}))
    assert s.database_url == "postgresql+asyncpg://dvd:dvd@localhost:5432/dvd"


def test_trailing_newline_survives_the_driver_normalization_too():
    # The exact failure mode: a plain postgresql:// URL (what a hosting
    # platform hands out) with a trailing newline, needing both fixes at once.
    s = Settings(**(BASE | {"DATABASE_URL": "postgresql://dvd:dvd@localhost:5432/dvd\n"}))
    assert s.database_url == "postgresql+asyncpg://dvd:dvd@localhost:5432/dvd"


def test_trailing_whitespace_on_a_secret_is_stripped():
    # No custom validator of its own — relies entirely on str_strip_whitespace.
    s = Settings(**(BASE | {"CDSE_CLIENT_SECRET": "super-secret \n"}))
    assert s.cdse_client_secret == "super-secret"


def test_leading_whitespace_is_also_stripped():
    s = Settings(**(BASE | {"DATABASE_URL": "  postgresql+asyncpg://dvd:dvd@localhost:5432/dvd"}))
    assert s.database_url == "postgresql+asyncpg://dvd:dvd@localhost:5432/dvd"
