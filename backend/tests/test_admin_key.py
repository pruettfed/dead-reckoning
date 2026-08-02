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


def test_non_ascii_header_is_rejected_not_crashed():
    # compare_digest raises TypeError on non-ASCII str; a bad header must be a
    # 401, never a 500.
    with pytest.raises(HTTPException) as exc:
        check_admin_key("naïve-ключ", "secret")
    assert exc.value.status_code == 401


def test_messages_are_parameterized_for_other_keys():
    with pytest.raises(HTTPException) as exc:
        check_admin_key(None, None, what="developer tools", setting="DEVTOOLS_API_KEY")
    assert exc.value.status_code == 503
    assert "DEVTOOLS_API_KEY" in exc.value.detail

    with pytest.raises(HTTPException) as exc:
        check_admin_key("wrong", "secret", header="X-Devtools-Key")
    assert exc.value.status_code == 401
    assert "X-Devtools-Key" in exc.value.detail
