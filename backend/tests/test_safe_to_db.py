"""Pure-function tests for the SAFE->dB calibration.

Exercises the math and parsing that turn raw GRD amplitude into sigma0-dB. The
rasterio/zipfile I/O (convert, _read_safe) needs a real .SAFE product and is
covered by the Colab smoke test, not here. Loaded by path — it lives in ml/.
"""

import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

from app.sar import DB_MAX, DB_MIN

_SPEC = importlib.util.spec_from_file_location(
    "safe_to_db", Path(__file__).resolve().parents[2] / "ml" / "safe_to_db.py"
)
safe_to_db = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(safe_to_db)


CALIB_XML = b"""<calibration>
  <calibrationVectorList count="2">
    <calibrationVector>
      <line>0</line>
      <pixel count="3">0 2 4</pixel>
      <sigmaNought count="3">10.0 10.0 10.0</sigmaNought>
    </calibrationVector>
    <calibrationVector>
      <line>4</line>
      <pixel count="3">0 2 4</pixel>
      <sigmaNought count="3">20.0 20.0 20.0</sigmaNought>
    </calibrationVector>
  </calibrationVectorList>
</calibration>
"""


class TestFindMembers:
    def test_picks_vv_measurement_and_calibration(self):
        names = [
            "S1A.SAFE/measurement/s1a-iw-grd-vv-20200126-001.tiff",
            "S1A.SAFE/measurement/s1a-iw-grd-vh-20200126-002.tiff",
            "S1A.SAFE/annotation/calibration/calibration-s1a-iw-grd-vv-001.xml",
            "S1A.SAFE/annotation/calibration/calibration-s1a-iw-grd-vh-002.xml",
            "S1A.SAFE/annotation/calibration/noise-s1a-iw-grd-vv-001.xml",
        ]
        measurement, calibration = safe_to_db.find_members(names)
        assert measurement.endswith("grd-vv-20200126-001.tiff")
        assert calibration.endswith("calibration-s1a-iw-grd-vv-001.xml")

    def test_missing_vv_measurement_aborts(self):
        with pytest.raises(SystemExit, match="no VV measurement"):
            safe_to_db.find_members(["x.SAFE/annotation/calibration/calibration-vv.xml"])


class TestParseCalibrationLut:
    def test_reads_lines_pixels_and_sigma(self):
        lines, pixels, sigmas = safe_to_db.parse_calibration_lut(CALIB_XML)
        assert lines.tolist() == [0.0, 4.0]
        assert pixels[0].tolist() == [0.0, 2.0, 4.0]
        assert sigmas[1].tolist() == [20.0, 20.0, 20.0]

    def test_vectors_are_sorted_by_line(self):
        swapped = CALIB_XML.replace(b"<line>0</line>", b"<line>9</line>")
        lines, _, sigmas = safe_to_db.parse_calibration_lut(swapped)
        assert lines[0] < lines[1]
        # the line=4 vector (sigma 20) now sorts first
        assert sigmas[0].tolist() == [20.0, 20.0, 20.0]

    def test_empty_vector_list_aborts(self):
        with pytest.raises(SystemExit, match="no calibrationVectorList"):
            safe_to_db.parse_calibration_lut(b"<calibration></calibration>")


class TestExpandLut:
    def test_row_interpolation_is_linear(self):
        lines, pixels, sigmas = safe_to_db.parse_calibration_lut(CALIB_XML)
        lut = safe_to_db.expand_lut(lines, pixels, sigmas, height=5, width=5)
        assert lut.shape == (5, 5)
        # sigma goes 10 at line 0 -> 20 at line 4, linearly
        assert lut[0].tolist() == pytest.approx([10.0] * 5)
        assert lut[4].tolist() == pytest.approx([20.0] * 5)
        assert lut[2].tolist() == pytest.approx([15.0] * 5)

    def test_column_interpolation(self):
        lines = np.array([0.0])
        pixels = [np.array([0.0, 4.0])]
        sigmas = [np.array([10.0, 30.0])]
        lut = safe_to_db.expand_lut(lines, pixels, sigmas, height=1, width=5)
        assert lut[0].tolist() == pytest.approx([10.0, 15.0, 20.0, 25.0, 30.0])

    def test_blocked_rows_match_the_full_grid(self):
        """convert() interpolates one row-stripe at a time to stay within RAM; a
        stripe must equal the same rows of the full grid, or blocks would seam."""
        lines, pixels, sigmas = safe_to_db.parse_calibration_lut(CALIB_XML)
        full = safe_to_db.expand_lut(lines, pixels, sigmas, height=5, width=5)
        columns = safe_to_db.column_lut(lines, pixels, sigmas, width=5)
        stripe = safe_to_db.row_lut(lines, columns, np.arange(2, 4))
        assert stripe == pytest.approx(full[2:4])


class TestCalibrateToDb:
    def test_matches_the_production_formula(self):
        # sigma0 = DN^2 / sigmaNought^2 ; dB = 10 log10(sigma0)
        dn = np.array([[100, 50]], dtype=np.uint16)
        lut = np.array([[10.0, 10.0]], dtype=np.float64)
        db = safe_to_db.calibrate_to_db(dn, lut)
        assert db[0, 0] == pytest.approx(10 * math.log10((100 / 10) ** 2))  # 20 dB
        assert db[0, 1] == pytest.approx(10 * math.log10((50 / 10) ** 2))   # ~13.98 dB

    def test_nodata_dn_becomes_nan(self):
        dn = np.array([[0, 100]], dtype=np.uint16)
        db = safe_to_db.calibrate_to_db(dn, np.array([[10.0, 10.0]]))
        assert math.isnan(db[0, 0])
        assert np.isfinite(db[0, 1])

    def test_output_is_the_quantity_the_evalscript_windows(self):
        """calibrate_to_db must produce dB in the same units DB_MIN/DB_MAX bound,
        so a chip rendered from it lands in the production uint8 distribution."""
        # a sea pixel near the window floor and a hull near the ceiling both survive
        dn = np.array([[6, 320]], dtype=np.uint16)  # ~ -24 dB and ~ +0 dB at sigmaNought 100
        db = safe_to_db.calibrate_to_db(dn, np.array([[100.0, 100.0]]))
        assert DB_MIN < db[0, 0] < DB_MAX + 5
        assert db[0, 1] == pytest.approx(10 * math.log10((320 / 100) ** 2), abs=1e-4)


class TestSceneIdMapping:
    def _labels(self, tmp_path):
        csv_path = tmp_path / "labels.csv"
        csv_path.write_text(
            "scene_id,GRD_product_identifier,detect_scene_row,detect_scene_column\n"
            "sceneAAA,S1A_IW_GRDH_1SDV_20200126T051926_x_y_z.SAFE,10,20\n"
        )
        return csv_path

    def test_maps_product_identifier_to_scene_id(self, tmp_path):
        got = safe_to_db.scene_id_for(
            "S1A_IW_GRDH_1SDV_20200126T051926_x_y_z.SAFE.zip", self._labels(tmp_path)
        )
        assert got == "sceneAAA"

    def test_unknown_product_aborts(self, tmp_path):
        with pytest.raises(SystemExit, match="matched no GRD_product_identifier"):
            safe_to_db.scene_id_for("S1A_NOPE.SAFE.zip", self._labels(tmp_path))

    def test_falls_back_to_stem_without_mapping_column(self, tmp_path):
        csv_path = tmp_path / "bare.csv"
        csv_path.write_text("scene_id,detect_scene_row,detect_scene_column\nfoo,1,2\n")
        assert safe_to_db.scene_id_for("S1A_XYZ.SAFE.zip", csv_path) == "S1A_XYZ"
