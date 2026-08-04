from datetime import datetime, timedelta, timezone

import pytest

from app import scheduler
from app.pipeline import NEXT_PASS_LOOKBACK_DAYS, SEARCH_WINDOW_DAYS
from app.scheduler import decide, recent_scenes, schedule_state, snapshot
from tests.test_pipeline import make_scene

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)

# Roughly what a mid-sized sar_bbox costs; the exact figure is irrelevant to these
# rules, only its size relative to the remaining ceiling.
COST = 55.0
CEILING = 24_000.0


def decision(status, *, spent=False, month_to_date=0.0, ceiling=CEILING):
    return decide(
        status,
        has_pu_spend=spent,
        month_to_date_pu=month_to_date,
        pu_cost=COST,
        ceiling=ceiling,
    )


class TestDecide:
    def test_new_scene_is_analyzed(self):
        assert decision(None).analyze

    def test_deleted_scene_that_already_spent_pu_is_not_retried(self):
        # A scene row that's gone (e.g. scripts/dev_reset.py) looks exactly
        # like a genuinely new pass — `status` alone can't tell them apart.
        # `pu_ledger` can: a reset erases the row but never the ledger, so a
        # scene reset after its pixels were already paid for must not
        # re-spend PU just because the row is missing.
        assert not decision(None, spent=True).analyze

    def test_processed_scene_is_skipped(self):
        # Re-analysing a stored scene is 0 PU but also 0 value.
        assert not decision("processed").analyze

    def test_in_flight_scene_is_skipped(self):
        assert not decision("processing").analyze

    def test_failed_scene_that_never_spent_pu_is_retried(self):
        # The failure preceded the pixel fetch (bad AIS state, DB blip), so the
        # retry is genuinely free.
        assert decision("failed", spent=False).analyze

    def test_failed_scene_that_already_spent_pu_is_not_retried(self):
        # The budget guard that matters: a post-fetch failure re-tried every
        # sweep would burn the month's PU unattended.
        assert not decision("failed", spent=True).analyze

    def test_new_scene_blocked_when_it_would_cross_the_ceiling(self):
        assert not decision(None, month_to_date=CEILING - COST / 2).analyze

    def test_free_retry_also_blocked_by_the_ceiling(self):
        # A retry costs a fresh fetch, so the ceiling applies to it too.
        assert not decision("failed", spent=False, month_to_date=CEILING).analyze

    def test_spend_exactly_reaching_the_ceiling_is_allowed(self):
        assert decision(None, month_to_date=CEILING - COST).analyze

    def test_ceiling_of_zero_refuses_everything(self):
        assert not decision(None, ceiling=0.0).analyze

    def test_skip_reasons_are_distinguishable(self):
        # The log line has to say which guard fired, or a stuck region is
        # indistinguishable from a healthy one.
        reasons = {
            decision("processed").reason,
            decision("processing").reason,
            decision("failed", spent=True).reason,
            decision(None, ceiling=0.0).reason,
        }
        assert len(reasons) == 4


class TestRecentScenes:
    def test_narrows_the_lookback_to_the_trigger_window(self):
        # The sweep fetches the longer lookback for the interval median. Passing
        # that straight through would let a survey ROI — which has no AIS bracket
        # to bound it — analyze fortnight-old imagery as if it were new.
        assert SEARCH_WINDOW_DAYS < NEXT_PASS_LOOKBACK_DAYS
        old = make_scene("old", NOW - timedelta(days=SEARCH_WINDOW_DAYS + 1))
        fresh = make_scene("fresh", NOW - timedelta(days=1))
        assert recent_scenes([old, fresh], NOW) == [fresh]

    def test_scene_on_the_boundary_is_kept(self):
        edge = make_scene("edge", NOW - timedelta(days=SEARCH_WINDOW_DAYS))
        assert recent_scenes([edge], NOW) == [edge]

    def test_empty_when_every_pass_is_stale(self):
        stale = make_scene("stale", NOW - timedelta(days=NEXT_PASS_LOOKBACK_DAYS))
        assert recent_scenes([stale], NOW) == []


@pytest.fixture
def swept():
    """Two regions as the scheduler's last sweep left them: catalog facts only."""
    scheduler._schedule.clear()
    scheduler._schedule.update(
        {
            "alpha": {
                "name": "alpha",
                "label": "Alpha",
                "mode": "fused",
                "latest_scene_sensed_at": (NOW - timedelta(hours=6)).isoformat(),
                "next_expected_at": (NOW + timedelta(hours=6)).isoformat(),
            },
            "beta": {
                "name": "beta",
                "label": "Beta",
                "mode": "survey",
                "latest_scene_sensed_at": None,
                "next_expected_at": None,
            },
        }
    )
    yield
    scheduler._schedule.clear()


class TestSnapshot:
    def test_reports_a_finished_analysis_without_waiting_for_the_next_sweep(self, swept):
        # The reported bug: the row was written before `await start_analysis`
        # and never rewritten, so a region that had just finished still read
        # "never analyzed" for up to a full sweep interval. `last_processed_at`
        # now comes from the database per request, so a sweep-old row is fine.
        finished_at = NOW - timedelta(minutes=2)
        rows = {r["name"]: r for r in snapshot({"alpha": finished_at}, NOW)}
        assert rows["alpha"]["last_processed_at"] == finished_at.isoformat()
        assert rows["alpha"]["state"] == "scheduled"

    def test_region_never_analyzed_reports_null(self, swept):
        rows = {r["name"]: r for r in snapshot({}, NOW)}
        assert rows["alpha"]["last_processed_at"] is None

    def test_state_is_derived_live_not_cached(self, swept):
        # Nothing in the stored row says "analyzing"; it comes from the
        # in-flight registry at read time.
        assert snapshot({}, NOW)[0]["state"] != "analyzing"
        rows = {r["name"]: r for r in snapshot({}, NOW + timedelta(hours=12))}
        # The cached estimate went stale in place; the read reflects that.
        assert rows["alpha"]["state"] == "awaiting_publication"

    def test_soonest_first_with_unestimable_regions_last(self, swept):
        assert [r["name"] for r in snapshot({}, NOW)] == ["alpha", "beta"]

    def test_empty_before_the_first_sweep(self):
        scheduler._schedule.clear()
        assert snapshot({}, NOW) == []


class TestScheduleState:
    def test_running_analysis_reports_analyzing(self):
        assert schedule_state(NOW + timedelta(hours=3), analyzing=True, now=NOW) == "analyzing"

    def test_future_pass_is_scheduled(self):
        assert schedule_state(NOW + timedelta(hours=3), analyzing=False, now=NOW) == "scheduled"

    def test_elapsed_pass_is_awaiting_publication(self):
        # Rows are cached from the last sweep, so an estimate goes stale in
        # place; GRD publication also lags acquisition by hours.
        assert (
            schedule_state(NOW - timedelta(minutes=5), analyzing=False, now=NOW)
            == "awaiting_publication"
        )

    def test_unestimable_pass_is_unknown(self):
        # estimate_next_pass returns None below three distinct passes.
        assert schedule_state(None, analyzing=False, now=NOW) == "unknown"

    def test_analyzing_wins_over_a_missing_estimate(self):
        assert schedule_state(None, analyzing=True, now=NOW) == "analyzing"
