"""Pure-function tests for the offline bench restretch / LEE. No DB, network, torch.

The DB path (`score`, `main`) defers its `app.database` import, so importing this
module — and these pure functions — needs no DATABASE_URL, matching the rest of the
suite.
"""

import numpy as np
import pytest

from scripts.bench_detector import lee_filter, render, restretch

# Sidecar from a wide calibration fetch: valid data occupies uint8 1-255.
CALIB_META = {"db_min": -35.0, "db_max": 5.0}


class TestRestretch:
    def test_identity_to_calibration_window_within_one_lsb(self):
        # Restretching back to the exact calibration window recovers the input up
        # to the 1/254-vs-255 encoding offset (< 1 dB quantization level).
        u = np.arange(1, 256, dtype=np.uint8).reshape(1, -1)
        out = restretch(u, CALIB_META, lo=-35.0, hi=5.0)
        assert np.all(np.abs(out.astype(int) - u.astype(int)) <= 1)

    def test_monotonic_in_input(self):
        u = np.arange(1, 256, dtype=np.uint8).reshape(1, -1)
        out = restretch(u, CALIB_META, lo=-30.0, hi=-5.0).ravel()
        assert np.all(np.diff(out.astype(int)) >= 0)

    def test_clamps_below_window_to_zero(self):
        # dB = -33 sits below a (-30, -10) window → floor.
        db = -33.0
        u = round(1 + 254 * (db - (-35.0)) / (5.0 - (-35.0)))
        pix = np.array([[u]], dtype=np.uint8)
        assert restretch(pix, CALIB_META, lo=-30.0, hi=-10.0)[0, 0] == 0

    def test_clamps_above_window_to_255(self):
        # dB = 0 sits above a (-30, -10) window → ceiling.
        db = 0.0
        u = round(1 + 254 * (db - (-35.0)) / (5.0 - (-35.0)))
        pix = np.array([[u]], dtype=np.uint8)
        assert restretch(pix, CALIB_META, lo=-30.0, hi=-10.0)[0, 0] == 255

    def test_rejects_window_outside_calibration(self):
        u = np.array([[128]], dtype=np.uint8)
        with pytest.raises(ValueError):
            restretch(u, CALIB_META, lo=-40.0, hi=0.0)  # lo below calibration LO
        with pytest.raises(ValueError):
            restretch(u, CALIB_META, lo=-10.0, hi=-20.0)  # hi <= lo


class TestLeeFilter:
    def test_constant_field_is_unchanged(self):
        # Zero local variance → zero weight → output is the (constant) local mean.
        a = np.full((8, 8), 3.0)
        assert np.allclose(lee_filter(a, 3), 3.0)

    def test_preserves_shape(self):
        a = np.random.default_rng(0).random((10, 12))
        assert lee_filter(a, 5).shape == a.shape


class TestRender:
    def test_no_lee_equals_restretch(self):
        u = np.arange(0, 256, dtype=np.uint8).reshape(16, 16)
        expected = restretch(u, CALIB_META, lo=-25.0, hi=0.0)
        assert np.array_equal(render(u, CALIB_META, lo=-25.0, hi=0.0, lee=0), expected)

    def test_output_is_uint8(self):
        u = np.arange(0, 256, dtype=np.uint8).reshape(16, 16)
        out = render(u, CALIB_META, lo=-25.0, hi=0.0, lee=3)
        assert out.dtype == np.uint8
