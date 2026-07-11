from datetime import datetime, timedelta, timezone

import pytest

from app import sources


@pytest.fixture(autouse=True)
def _reset():
    sources._reset_for_tests()
    yield
    sources._reset_for_tests()


def test_snapshot_empty_when_no_sources_registered():
    assert sources.snapshot(stale_after=60) == {}


def test_mark_connected_sets_state_and_connected_since():
    sources.mark_connected("ais")
    snap = sources.snapshot(stale_after=60)
    assert snap["ais"]["state"] == "connected"
    assert snap["ais"]["connected_since"] is not None
    assert snap["ais"]["reconnect_count"] == 0


def test_reconnect_count_only_increments_on_subsequent_connects():
    sources.mark_connected("ais")
    assert sources.snapshot(stale_after=60)["ais"]["reconnect_count"] == 0

    sources.mark_disconnected("ais", reason="network")
    sources.mark_connected("ais")
    assert sources.snapshot(stale_after=60)["ais"]["reconnect_count"] == 1

    sources.mark_disconnected("ais", reason="network")
    sources.mark_connected("ais")
    assert sources.snapshot(stale_after=60)["ais"]["reconnect_count"] == 2


def test_mark_message_updates_last_message_at():
    sources.mark_connected("ais")
    sources.mark_message("ais")
    snap = sources.snapshot(stale_after=60)
    assert snap["ais"]["last_message_at"] is not None
    assert snap["ais"]["lag_seconds"] is not None


def test_mark_disconnected_clears_connected_since_and_records_reason():
    sources.mark_connected("ais")
    sources.mark_disconnected("ais", reason="websocket closed")
    snap = sources.snapshot(stale_after=60)
    assert snap["ais"]["state"] == "disconnected"
    assert snap["ais"]["connected_since"] is None
    assert snap["ais"]["last_error"] == "websocket closed"


def test_mark_error_increments_count_and_does_not_change_state():
    sources.mark_connected("ais")
    sources.mark_error("ais", "flush failed: connection reset")
    snap = sources.snapshot(stale_after=60)
    assert snap["ais"]["state"] == "connected"
    assert snap["ais"]["error_count"] == 1
    assert snap["ais"]["last_error"] == "flush failed: connection reset"

    sources.mark_error("ais", "another one")
    snap = sources.snapshot(stale_after=60)
    assert snap["ais"]["error_count"] == 2


def test_snapshot_derives_stale_when_connected_and_lag_exceeds_threshold():
    sources.mark_connected("ais")
    sources.mark_message("ais")
    now = sources._STATE["ais"].last_message_at + timedelta(seconds=90)
    snap = sources.snapshot(stale_after=60, _now=now)
    assert snap["ais"]["state"] == "stale"
    assert snap["ais"]["lag_seconds"] == 90.0


def test_snapshot_stays_connected_when_lag_within_threshold():
    sources.mark_connected("ais")
    sources.mark_message("ais")
    now = sources._STATE["ais"].last_message_at + timedelta(seconds=30)
    snap = sources.snapshot(stale_after=60, _now=now)
    assert snap["ais"]["state"] == "connected"
    assert snap["ais"]["lag_seconds"] == 30.0


def test_snapshot_does_not_derive_stale_when_disconnected():
    sources.mark_disconnected("ais", reason="initial")
    snap = sources.snapshot(stale_after=60)
    assert snap["ais"]["state"] == "disconnected"


def test_snapshot_lag_rounded_to_one_decimal():
    sources.mark_connected("ais")
    sources.mark_message("ais")
    # 5.456789s after the last message → should round to 5.5
    now = sources._STATE["ais"].last_message_at + timedelta(seconds=5, microseconds=456789)
    snap = sources.snapshot(stale_after=60, _now=now)
    assert snap["ais"]["lag_seconds"] == 5.5


def test_snapshot_handles_unknown_source_without_message():
    # mark_disconnected on a never-seen source should still produce a sane snapshot
    sources.mark_disconnected("sar_sentinel1", reason="not implemented yet")
    snap = sources.snapshot(stale_after=60)
    assert snap["sar_sentinel1"]["state"] == "disconnected"
    assert snap["sar_sentinel1"]["lag_seconds"] is None
    assert snap["sar_sentinel1"]["last_message_at"] is None


def test_multiple_sources_tracked_independently():
    sources.mark_connected("ais")
    sources.mark_message("ais")
    sources.mark_disconnected("sar_sentinel1", reason="offline")
    snap = sources.snapshot(stale_after=60)
    assert snap["ais"]["state"] == "connected"
    assert snap["sar_sentinel1"]["state"] == "disconnected"
    assert set(snap.keys()) == {"ais", "sar_sentinel1"}
