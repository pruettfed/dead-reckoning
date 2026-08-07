from datetime import datetime, timedelta, timezone

import pytest

from app import scheduler
from app.pipeline import NEXT_PASS_LOOKBACK_DAYS, SEARCH_WINDOW_DAYS
from app.scheduler import (
    AisHealth,
    decide,
    gap_bucket_count,
    recent_scenes,
    schedule_state,
    snapshot,
    warmup_gate,
    warmup_ready,
)
from app.rois import ROIS
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
    scheduler._held = {}


class TestSnapshot:
    def test_held_regions_report_warming_up_individually(self, swept):
        # The split the whole change exists for: with AIS down, the fused half
        # is held while the survey half keeps its countdown. A global flag
        # would have painted both.
        scheduler._held = {"alpha": "AIS silent for 4.1h"}
        rows = {r["name"]: r for r in snapshot({}, NOW)}
        assert rows["alpha"]["state"] == "warming_up"
        assert rows["beta"]["state"] != "warming_up"

    def test_no_region_warms_up_once_the_gate_clears(self, swept):
        scheduler._held = {}
        assert all(r["state"] != "warming_up" for r in snapshot({}, NOW))
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

    def test_warming_up_overrides_a_pass_estimate(self):
        # The catalog may well have a pass due, but nothing can be analyzed
        # until the gate opens — a countdown here would promise work the
        # scheduler is deliberately holding back.
        assert (
            schedule_state(
                NOW + timedelta(hours=3), analyzing=False, now=NOW, warming_up=True
            )
            == "warming_up"
        )

    def test_an_analysis_already_running_outranks_warming_up(self):
        assert (
            schedule_state(None, analyzing=True, now=NOW, warming_up=True) == "analyzing"
        )


REQUIRED_H = 6.0
MAX_WAIT_H = 8.0


def warmup(min_ais_time, *, waited_hours=0.0):
    return warmup_ready(
        min_ais_time,
        started_at=NOW - timedelta(hours=waited_hours),
        now=NOW,
        required_hours=REQUIRED_H,
        max_wait_hours=MAX_WAIT_H,
    )


class TestSurveyWarmupReady:
    """The survey-mode verdict: buffer depth, capped and released by design.

    Survey ROIs skip fusion entirely, so they are correct with no AIS at all —
    the cap exists to release them on a deployment with no AISSTREAM_API_KEY.
    Fused ROIs are gated by `warmup_gate` instead, which has no cap.
    """

    def test_deep_buffer_starts_immediately(self):
        # The ordinary redeploy: the database already holds AIS, so the
        # scheduler must not sit out another six hours for no reason.
        ready, detail = warmup(NOW - timedelta(hours=30))
        assert ready
        assert "30.0h deep" in detail

    def test_buffer_exactly_at_the_threshold_is_ready(self):
        assert warmup(NOW - timedelta(hours=REQUIRED_H))[0]

    def test_shallow_buffer_waits(self):
        ready, detail = warmup(NOW - timedelta(hours=2))
        assert not ready
        assert "need 6h" in detail

    def test_empty_buffer_waits(self):
        # A fresh database on a cold deploy. This is the case that used to buy
        # pixels for six survey regions on the first sweep.
        ready, detail = warmup(None)
        assert not ready
        assert "no AIS recorded yet" in detail

    def test_empty_buffer_starts_once_the_cap_expires(self):
        # AISSTREAM_API_KEY unset means min(time) is NULL forever. Survey
        # regions need no AIS, so the cap has to release them.
        ready, detail = warmup(None, waited_hours=MAX_WAIT_H)
        assert ready
        assert "without a full AIS buffer" in detail

    def test_shallow_buffer_also_starts_at_the_cap(self):
        assert warmup(NOW - timedelta(hours=1), waited_hours=MAX_WAIT_H + 1)[0]

    def test_depth_is_preferred_to_the_cap_when_both_hold(self):
        # Both conditions are satisfied; the reported reason should be the
        # good one, not the resigned one.
        ready, detail = warmup(NOW - timedelta(hours=30), waited_hours=MAX_WAIT_H + 1)
        assert ready
        assert "deep" in detail and "without a full" not in detail

    def test_a_zero_requirement_starts_at_once(self):
        # How local dev is configured (docker-compose sets 0), so the gate must
        # be a no-op there even with an empty database.
        ready, _ = warmup_ready(
            None,
            started_at=NOW,
            now=NOW,
            required_hours=0.0,
            max_wait_hours=0.0,
        )
        assert ready

    def test_zero_required_hours_ignores_the_cap(self):
        # docker-compose.yml sets SCHEDULER_WARMUP_HOURS: 0 and *nothing else*,
        # so the cap stays at its 8h default. Zeroing both (above) hid this:
        # with an empty database the depth branch is skipped and dev used to
        # block on the cap for eight hours.
        ready, _ = warmup_ready(
            None,
            started_at=NOW,
            now=NOW,
            required_hours=0.0,
            max_wait_hours=MAX_WAIT_H,
        )
        assert ready


GAP_M = 30.0


def health(*, min_h=None, max_h=None, gap_end_h=None):
    """An AisHealth in hours-before-NOW, so the tests read as timelines."""
    at = lambda h: None if h is None else NOW - timedelta(hours=h)  # noqa: E731
    return AisHealth(min_time=at(min_h), max_time=at(max_h), last_gap_end=at(gap_end_h))


def gate(h, *, waited_hours=0.0, required_hours=REQUIRED_H):
    return warmup_gate(
        h,
        started_at=NOW - timedelta(hours=waited_hours),
        now=NOW,
        required_hours=required_hours,
        max_wait_hours=MAX_WAIT_H,
        gap_minutes=GAP_M,
    )


class TestGapBucketCount:
    def test_six_hours_of_half_hour_buckets(self):
        assert gap_bucket_count(6.0, 30.0) == 12

    def test_a_partial_bucket_rounds_up(self):
        # Under-covering the window would let a gap hide in the remainder.
        assert gap_bucket_count(1.0, 45.0) == 2

    def test_zero_required_hours_probes_nothing(self):
        assert gap_bucket_count(0.0, 30.0) == 0

    def test_a_non_positive_bucket_probes_nothing(self):
        # Guards a division by zero on a misconfigured bucket width.
        assert gap_bucket_count(6.0, 0.0) == 0


class TestFusedWarmupGate:
    def test_continuous_and_fresh_is_ready(self):
        g = gate(health(min_h=30, max_h=0, gap_end_h=None))
        assert g.fused_ready
        assert "continuous" in g.fused_detail

    def test_the_cap_never_releases_a_fused_roi(self):
        # The headline of this change. With AISStream down, the 8h cap used to
        # release every region and sweep with no AIS at all; a fused ROI fused
        # against an empty buffer calls every vessel dark.
        g = gate(health(), waited_hours=MAX_WAIT_H * 10)
        assert not g.fused_ready
        assert "no AIS recorded at all" in g.fused_detail

    def test_a_silent_stream_holds_even_with_no_gap_in_the_window(self):
        # AIS stopped 3h ago. Nothing inside the probed window contradicts it,
        # so only the freshness check catches this — the case that produced a
        # confidently-wrong all-dark scene.
        g = gate(health(min_h=48, max_h=3, gap_end_h=None))
        assert not g.fused_ready
        assert "silent for 3.0h" in g.fused_detail

    def test_a_deep_buffer_does_not_excuse_a_stale_one(self):
        # Exactly the shape retention leaves after an outage: rows from before
        # it survive the 2-day prune, so depth still reads ~2 days.
        g = gate(health(min_h=48, max_h=12, gap_end_h=None))
        assert not g.fused_ready

    def test_a_recent_gap_restarts_the_countdown(self):
        # The stream recovered ten minutes ago after an outage. Depth from
        # min(time) would say "days"; continuity says ten minutes.
        g = gate(health(min_h=48, max_h=0, gap_end_h=1 / 6))
        assert not g.fused_ready
        assert "need 6h" in g.fused_detail

    def test_continuity_exactly_at_the_threshold_is_ready(self):
        assert gate(health(min_h=48, max_h=0, gap_end_h=REQUIRED_H)).fused_ready

    def test_no_ais_at_all_holds(self):
        assert not gate(health()).fused_ready

    def test_a_zero_requirement_disables_the_gate(self):
        # Local dev, where compose sets SCHEDULER_WARMUP_HOURS: 0.
        g = gate(health(), required_hours=0.0)
        assert g.fused_ready


class TestGateSplit:
    def test_a_dead_stream_past_the_cap_releases_survey_but_holds_fused(self):
        # The whole point of the split: survey ROIs skip fusion and are correct
        # without AIS, so the cap must still release them.
        g = gate(health(), waited_hours=MAX_WAIT_H + 1)
        assert g.survey_ready
        assert not g.fused_ready

    def test_ready_routes_by_mode(self):
        g = gate(health(), waited_hours=MAX_WAIT_H + 1)
        assert g.ready("survey")
        assert not g.ready("fused")

    def test_the_two_details_are_distinguishable(self):
        # Both reasons reach the logs and /api/analysis/schedule; a reader has
        # to be able to tell which half of the fleet is held and why.
        g = gate(health(), waited_hours=MAX_WAIT_H + 1)
        assert g.fused_detail != g.survey_detail

    def test_a_healthy_stream_releases_both(self):
        g = gate(health(min_h=30, max_h=0, gap_end_h=None))
        assert g.fused_ready and g.survey_ready


class _FakeSettings:
    """Only the fields _apply_gate reads."""

    scheduler_interval_seconds = 900.0


class TestApplyGate:
    """The gate mapped onto the real registry — which regions actually stop."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        yield
        scheduler._held = {}
        scheduler._status.update(state="starting", detail="")

    def test_a_dead_stream_holds_the_fused_half_and_only_that_half(self):
        held = scheduler._apply_gate(
            gate(health(), waited_hours=MAX_WAIT_H + 1), _FakeSettings()
        )
        modes = {ROIS[name].mode for name in held}
        assert modes == {"fused"}
        assert len(held) == sum(1 for r in ROIS.values() if r.mode == "fused")
        # Half the fleet still working is "running", not "warming_up" — the
        # status has to name the split rather than imply a dead scheduler.
        assert scheduler._status["state"] == "running"
        assert "HELD" in scheduler._status["detail"]

    def test_a_healthy_stream_holds_nobody(self):
        held = scheduler._apply_gate(
            gate(health(min_h=30, max_h=0, gap_end_h=None)), _FakeSettings()
        )
        assert held == {}
        assert scheduler._status["state"] == "running"
        assert "HELD" not in scheduler._status["detail"]

    def test_everything_held_reports_warming_up(self):
        # Cold boot: no AIS yet and the survey cap has not expired either.
        scheduler._apply_gate(gate(health()), _FakeSettings())
        assert scheduler._status["state"] == "warming_up"
        assert len(scheduler._held) == len(ROIS)

    def test_held_is_rebound_not_mutated(self):
        # `snapshot` binds this dict on the request path while the sweep
        # rewrites it; mutating in place would expose an empty gate mid-read.
        scheduler._apply_gate(gate(health()), _FakeSettings())
        first = scheduler._held
        scheduler._apply_gate(
            gate(health(min_h=30, max_h=0, gap_end_h=None)), _FakeSettings()
        )
        assert scheduler._held is not first
        assert first, "the old dict must keep its contents for any in-flight reader"
