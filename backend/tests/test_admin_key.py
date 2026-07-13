"""Tests for the analysis-endpoint admin key check."""

import pytest
from fastapi import HTTPException

from app.main import check_admin_key


def test_unconfigured_key_disables_endpoint():
    with pytest.raises(HTTPException) as exc:
        check_admin_key("anything", None)
    assert exc.value.status_code == 503


def test_missing_header_rejected():
    with pytest.raises(HTTPException) as exc:
        check_admin_key(None, "secret")
    assert exc.value.status_code == 401


def test_wrong_key_rejected():
    with pytest.raises(HTTPException) as exc:
        check_admin_key("wrong", "secret")
    assert exc.value.status_code == 401


def test_correct_key_passes():
    check_admin_key("secret", "secret")
