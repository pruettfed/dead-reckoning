"""Tests for the fusion coverage guard. The SQL itself (ST_DWithin matching,
footprint clip) is DB-integration territory, out of scope for this suite."""

from datetime import datetime, timedelta, timezone

from app.fusion import coverage_ok

SENSED_AT = datetime(2026, 7, 10, 2, 30, tzinfo=timezone.utc)


def test_no_ais_data_means_no_coverage():
    assert coverage_ok(SENSED_AT, None, window_hours=2) is False


def test_buffer_reaching_past_window_is_covered():
    min_ais = SENSED_AT - timedelta(hours=10)
    assert coverage_ok(SENSED_AT, min_ais, window_hours=2) is True


def test_buffer_starting_inside_window_is_not_covered():
    min_ais = SENSED_AT - timedelta(hours=1)
    assert coverage_ok(SENSED_AT, min_ais, window_hours=2) is False


def test_exact_window_boundary_is_covered():
    min_ais = SENSED_AT - timedelta(hours=2)
    assert coverage_ok(SENSED_AT, min_ais, window_hours=2) is True


def test_scene_older_than_buffer_is_not_covered():
    min_ais = SENSED_AT + timedelta(hours=5)  # buffer starts after the scene
    assert coverage_ok(SENSED_AT, min_ais, window_hours=2) is False
