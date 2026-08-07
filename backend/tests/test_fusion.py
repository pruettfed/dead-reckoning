"""Tests for the fusion coverage guard, one-to-one assignment and classification.
The SQL (dead reckoning, envelope, chance-match probe) is DB-integration territory."""

from datetime import datetime, timedelta, timezone

from app.fusion import MatchCandidate, assign_one_to_one, classify, coverage_ok

SENSED_AT = datetime(2026, 7, 10, 2, 30, tzinfo=timezone.utc)

# Far enough past the scene that the ceiling is satisfied, so each floor test
# below still measures only the floor.
FRESH_MAX = SENSED_AT + timedelta(hours=10)


def test_no_ais_data_means_no_coverage():
    assert coverage_ok(SENSED_AT, None, None, window_hours=2) is False


def test_buffer_reaching_past_window_is_covered():
    min_ais = SENSED_AT - timedelta(hours=10)
    assert coverage_ok(SENSED_AT, min_ais, FRESH_MAX, window_hours=2) is True


def test_buffer_starting_inside_window_is_not_covered():
    min_ais = SENSED_AT - timedelta(hours=1)
    assert coverage_ok(SENSED_AT, min_ais, FRESH_MAX, window_hours=2) is False


def test_exact_window_boundary_is_covered():
    min_ais = SENSED_AT - timedelta(hours=2)
    assert coverage_ok(SENSED_AT, min_ais, FRESH_MAX, window_hours=2) is True


def test_scene_older_than_buffer_is_not_covered():
    min_ais = SENSED_AT + timedelta(hours=5)  # buffer starts after the scene
    assert coverage_ok(SENSED_AT, min_ais, FRESH_MAX, window_hours=2) is False


def test_a_buffer_that_stopped_before_the_scene_is_not_covered():
    # The stale-AIS hole: ingest died, but rows from days earlier survive the
    # retention prune, so the floor alone still passes. Fusion would then match
    # nothing, measure 0% chance-match on empty water, read as discriminating,
    # and call every vessel dark.
    min_ais = SENSED_AT - timedelta(days=2)
    max_ais = SENSED_AT - timedelta(hours=6)
    assert coverage_ok(SENSED_AT, min_ais, max_ais, window_hours=2) is False


def test_no_max_ais_time_means_no_coverage():
    min_ais = SENSED_AT - timedelta(hours=10)
    assert coverage_ok(SENSED_AT, min_ais, None, window_hours=2) is False


def test_exact_ceiling_boundary_is_covered():
    min_ais = SENSED_AT - timedelta(hours=10)
    max_ais = SENSED_AT + timedelta(hours=2)
    assert coverage_ok(SENSED_AT, min_ais, max_ais, window_hours=2) is True


def candidate(det_id, mmsi, distance_m, time_delta_s=0.0):
    return MatchCandidate(
        det_id=det_id, mmsi=mmsi, distance_m=distance_m, time_delta_s=time_delta_s
    )


# --- one-to-one assignment ------------------------------------------------
# Regression: the old matcher gave MMSI 416042000 to two detections 4.2 km apart.

def test_one_vessel_cannot_claim_two_detections():
    assigned = assign_one_to_one([
        candidate(1, 416042000, 235.0),
        candidate(2, 416042000, 106.0),
    ])
    assert set(assigned) == {2}
    assert assigned[2].mmsi == 416042000


def test_closest_pair_wins_and_the_loser_falls_through():
    """The far detection must not silently inherit the vessel it lost."""
    assigned = assign_one_to_one([
        candidate(1, 111, 900.0),
        candidate(2, 111, 20.0),
        candidate(1, 222, 950.0),
    ])
    assert assigned[2].mmsi == 111
    assert assigned[1].mmsi == 222


def test_detection_keeps_only_its_nearest_candidate():
    assigned = assign_one_to_one([
        candidate(1, 111, 400.0),
        candidate(1, 222, 50.0),
    ])
    assert assigned[1].mmsi == 222


def test_assignment_is_order_independent():
    pairs = [candidate(1, 111, 300.0), candidate(2, 111, 90.0), candidate(2, 222, 310.0)]
    assert assign_one_to_one(pairs) == assign_one_to_one(list(reversed(pairs)))


def test_equal_distances_break_deterministically_on_id():
    assigned = assign_one_to_one([
        candidate(2, 111, 100.0),
        candidate(1, 111, 100.0),
    ])
    assert set(assigned) == {1}


def test_no_candidates_assigns_nothing():
    assert assign_one_to_one([]) == {}


# --- three-state classification -------------------------------------------

def test_assigned_detection_is_matched():
    assigned = {7: candidate(7, 111, 40.0)}
    assert classify(7, assigned, margin_m=-500.0, discriminating=True) == "matched"


def test_unmatched_outside_every_envelope_is_dark():
    assert classify(7, {}, margin_m=942.0, discriminating=True) == "dark"


def test_unmatched_inside_an_envelope_is_indeterminate():
    """Inside a vessel's uncertainty budget — unproven, so not a dark claim."""
    assert classify(7, {}, margin_m=-30.0, discriminating=True) == "indeterminate"


def test_envelope_boundary_is_indeterminate_not_dark():
    assert classify(7, {}, margin_m=0.0, discriminating=True) == "indeterminate"


def test_no_ais_candidate_at_all_is_indeterminate_not_dark():
    """An empty AIS neighbourhood is missing evidence, not evidence of absence."""
    assert classify(7, {}, margin_m=None, discriminating=True) == "indeterminate"


def test_non_discriminating_scene_withholds_every_dark_call():
    """The publish gate: if empty water matches too often, no dark is reported."""
    assert classify(7, {}, margin_m=5000.0, discriminating=False) == "indeterminate"


def test_non_discriminating_scene_still_reports_matches():
    assigned = {7: candidate(7, 111, 40.0)}
    assert classify(7, assigned, margin_m=-500.0, discriminating=False) == "matched"
