"""Tests for the `_vessel_dict` row -> API dict helper."""

from app.main import _vessel_dict


def test_resolvable_nav_status_adds_label():
    row = {"mmsi": 123, "sog": 5.0, "nav_status": 1}
    d = _vessel_dict(row)
    assert d["status"] == "at anchor"
    assert "nav_status" not in d


def test_none_nav_status_omits_status():
    row = {"mmsi": 123, "sog": 5.0, "nav_status": None}
    d = _vessel_dict(row)
    assert "status" not in d
    assert "nav_status" not in d


def test_not_available_nav_status_omits_status():
    row = {"mmsi": 123, "sog": 5.0, "nav_status": 15}
    d = _vessel_dict(row)
    assert "status" not in d


def test_other_fields_pass_through_unchanged():
    row = {"mmsi": 123, "sog": 5.0, "nav_status": 1}
    d = _vessel_dict(row)
    assert d["mmsi"] == 123
    assert d["sog"] == 5.0
