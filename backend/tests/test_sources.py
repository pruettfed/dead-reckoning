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


class _FakeSettings:
    """Only the fields redact reads."""

    aisstream_api_key = "super-secret-ais-key"
    cdse_client_secret = "super-secret-cdse"
    cdse_client_id = "client-id-1234"
    analysis_api_key = None
    devtools_api_key = None
    database_url = "postgresql+asyncpg://dvd:hunter2@db:5432/dvd"
    is_production = False
    source_stale_after_seconds = 60.0


@pytest.fixture
def _fake_secrets(monkeypatch):
    monkeypatch.setattr(sources, "get_settings", lambda: _FakeSettings())


def test_last_error_redacts_configured_secrets(_fake_secrets):
    # The AISStream key travels inside the subscribe frame, and last_error is
    # served by the unauthenticated /api/health.
    sources.mark_disconnected(
        "ais", reason="handshake failed sending APIKey=super-secret-ais-key"
    )
    last_error = sources.snapshot(stale_after=60)["ais"]["last_error"]
    assert "super-secret-ais-key" not in last_error
    assert "***" in last_error
    assert "handshake failed" in last_error  # the diagnostic itself survives


def test_mark_error_redacts_too(_fake_secrets):
    sources.mark_error("sar_sentinel1", "401 for secret super-secret-cdse")
    assert "super-secret-cdse" not in sources.snapshot(stale_after=60)["sar_sentinel1"]["last_error"]


def test_redaction_leaves_ordinary_errors_alone(_fake_secrets):
    sources.mark_disconnected("ais", reason="connection reset by peer")
    assert sources.snapshot(stale_after=60)["ais"]["last_error"] == "connection reset by peer"


def test_database_password_is_redacted(_fake_secrets):
    # asyncpg and SQLAlchemy both echo the DSN on a connection failure, and
    # every exception raised inside _run_analysis reaches this field.
    reason = "could not connect to postgresql+asyncpg://dvd:hunter2@db:5432/dvd"
    assert "hunter2" not in sources.redact(reason)


def test_credentials_in_an_unfamiliar_url_are_still_redacted(_fake_secrets):
    # SQLAlchemy rewrites the URL it reports, so it does not always match
    # settings.database_url verbatim — the pattern has to carry that case.
    assert "s3cret" not in sources.redact("failed on postgresql://admin:s3cret@other:5432/x")


def test_a_secret_containing_another_leaves_no_fragment(_fake_secrets):
    # Longest-first ordering: redacting the shorter one first would leave the
    # remainder of the longer one in the string.
    assert "super-secret-cdse" not in sources.redact("boom super-secret-cdse-extended")


def test_last_error_is_withheld_in_production(monkeypatch):
    class _Prod(_FakeSettings):
        is_production = True

    monkeypatch.setattr(sources, "get_settings", lambda: _Prod())
    sources.mark_error("ais", "some internal detail")
    snap = sources.snapshot(stale_after=60)["ais"]
    # The count still says something is wrong; the text stays in the logs.
    assert "last_error" not in snap
    assert snap["error_count"] == 1
