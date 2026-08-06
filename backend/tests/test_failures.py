"""The classifier that keeps raw exception text off the unauthenticated /api/scenes."""

from app.failures import UNKNOWN, classify


class TestClassify:
    def test_a_scene_that_did_not_fail_has_no_reason(self):
        assert classify(None) is None
        assert classify("") is None

    def test_coverage_failure(self):
        assert classify("fetched chip is only 43% real data, need 85%") == "Swath missed the box"

    def test_credentials_failure(self):
        assert classify("401 Unauthorized for url ...") == "Imagery access rejected"

    def test_detector_missing(self):
        assert classify("model checkpoint not found at 'models/sar_ship.pt'") == "Detector unavailable"

    def test_detector_oom(self):
        assert (
            classify("detection subprocess died on a 8000x6000 chip ... killed for memory")
            == "Detector ran out of memory"
        )

    def test_restart(self):
        assert classify("interrupted by restart") == "Interrupted by a restart"

    def test_ais_failure(self):
        assert classify("no AIS positions recorded inside 'north_taiwan'") == "No AIS reference in window"

    def test_matching_is_case_insensitive(self):
        assert classify("TIMED OUT waiting for CDSE") == "Imagery fetch timed out"

    def test_unrecognised_errors_do_not_echo_their_text(self):
        # The point of the whole module: an unfamiliar failure must degrade to
        # the generic phrase, never fall back to the exception string.
        raw = "asyncpg.InvalidPasswordError: password authentication failed for user 'dvd'"
        assert classify(raw) == UNKNOWN
        assert "dvd" not in classify(raw)

    def test_a_url_in_an_unknown_error_is_not_echoed(self):
        raw = "GET https://sh.dataspace.copernicus.eu/api/v1/process?token=abc123 failed"
        assert "copernicus" not in classify(raw)
        assert "abc123" not in classify(raw)

    def test_specific_causes_win_over_the_generic_ais_token(self):
        # "credential" and "ais" can both appear in one message; the more
        # specific diagnosis is the useful one.
        assert classify("credential rejected while fetching AIS") == "Imagery access rejected"
