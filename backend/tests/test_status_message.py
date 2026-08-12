"""validate_post is the only pure logic in app/status_message.py — everything
else is SQL the no-DB test suite can't reach (same split as devtools.py's
reset_* functions, see test_devtools_gate.py's docstring)."""

import pytest

from app.status_message import VALID_LEVELS, validate_post


def test_accepts_a_valid_message_and_level():
    validate_post("AIS ingest degraded, investigating", "warning")  # no raise


def test_rejects_a_blank_message():
    with pytest.raises(ValueError, match="blank"):
        validate_post("", "warning")


def test_rejects_a_whitespace_only_message():
    with pytest.raises(ValueError, match="blank"):
        validate_post("   ", "warning")


def test_rejects_an_unknown_level():
    with pytest.raises(ValueError, match="level"):
        validate_post("hello", "urgent")


def test_every_valid_level_is_accepted():
    for level in VALID_LEVELS:
        validate_post("hello", level)  # no raise


def test_accepts_a_valid_title():
    validate_post("hello", "warning", "OUTAGE")  # no raise


def test_rejects_a_blank_title():
    with pytest.raises(ValueError, match="blank"):
        validate_post("hello", "warning", "   ")


def test_rejects_an_overlong_title():
    with pytest.raises(ValueError, match="24 characters"):
        validate_post("hello", "warning", "x" * 25)
