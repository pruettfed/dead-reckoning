"""Pure-function tests for the scene transfer wire format. No DB, no network.

`_run_export`/`_run_import` defer their `app.database` import, so importing this
module exercises only the serialisation half — same arrangement as
tests/test_bench_detector.py.
"""

import base64
import json
from datetime import datetime, timezone

import pytest

from app.models import SarDetection, SarSceneRow, ShipMetadata
from scripts.transfer_scenes import (
    DETECTION_COLUMNS,
    FORMAT_VERSION,
    SCENE_COLUMNS,
    SHIP_COLUMNS,
    decode_stream,
    detection_to_record,
    encode_stream,
    read_stream,
    record_to_detection_params,
    record_to_scene_params,
    record_to_ship_params,
    scene_to_record,
    ship_to_record,
    validate,
)

# Shaped like a real export row: the SELECT aliases ST_AsEWKT(footprint) to
# footprint_ewkt and leaves overview_png as raw bytes.
SCENE_ROW = {
    "id": "68980792-ace2-4692-83c7-94fc891763e5",
    "name": "S1A_IW_GRDH_1SDV_20260803T101112_20260803T101137_001_002_ABCD.SAFE",
    "roi": "north_taiwan",
    "sensed_at": datetime(2026, 8, 3, 10, 11, 12, tzinfo=timezone.utc),
    "platform": "S1A",
    "status": "processed",
    "processed_at": datetime(2026, 8, 3, 13, 30, 0, tzinfo=timezone.utc),
    "error": None,
    "imaged_bbox": [120.0, 24.5, 121.5, 25.5],
    "chance_match_rate": 0.018018018018018018,
    "recall_large_total": 12,
    "recall_large_detected": 9,
    "footprint_ewkt": "SRID=4326;POLYGON((120 24,121 24,121 25,120 25,120 24))",
    "overview_png": b"\x89PNG\r\n\x1a\n\x00\x01\x02\xff",
}

DETECTION_ROW = {
    "scene_id": SCENE_ROW["id"],
    "confidence": 0.87,
    "confidence_bucket": "high",
    "match_state": "dark",
    "is_dark": True,
    "matched_mmsi": None,
    "candidate_mmsi": 416000000,
    "match_distance_m": None,
    "match_time_delta_s": -120.5,
    "dark_margin_m": 315.25,
    "on_land": False,
    "location_ewkt": "SRID=4326;POINT(120.5 25)",
}

SHIP_ROW = {
    "mmsi": 416000000,
    "ship_name": "TEST VESSEL",
    "ship_type": 70,
    "callsign": "BXYZ",
    "last_updated": datetime(2026, 8, 3, 9, 0, 0, tzinfo=timezone.utc),
}


def meta(**counts):
    return {
        "kind": "meta",
        "version": FORMAT_VERSION,
        "exported_at": "2026-08-14T00:00:00+00:00",
        "counts": counts,
    }


class TestSceneRoundTrip:
    def test_restores_every_column_except_the_encoded_ones(self):
        params = record_to_scene_params(scene_to_record(SCENE_ROW))
        for column, expected in SCENE_ROW.items():
            if column == "overview_png":
                continue
            assert params[column] == expected, column

    def test_png_bytes_survive_base64(self):
        record = scene_to_record(SCENE_ROW)
        assert isinstance(record["overview_png_b64"], str)
        assert record_to_scene_params(record)["overview_png"] == SCENE_ROW["overview_png"]

    def test_nullable_columns_stay_null(self):
        row = SCENE_ROW | {"overview_png": None, "imaged_bbox": None, "processed_at": None}
        params = record_to_scene_params(scene_to_record(row))
        assert params["overview_png"] is None
        assert params["imaged_bbox"] is None
        assert params["processed_at"] is None

    def test_timestamps_keep_their_utc_offset(self):
        params = record_to_scene_params(scene_to_record(SCENE_ROW))
        assert params["sensed_at"].utcoffset().total_seconds() == 0
        assert params["sensed_at"] == SCENE_ROW["sensed_at"]

    def test_record_is_json_serialisable(self):
        json.dumps(scene_to_record(SCENE_ROW))


class TestDetectionRoundTrip:
    def test_restores_every_column(self):
        params = record_to_detection_params(detection_to_record(DETECTION_ROW))
        for column, expected in DETECTION_ROW.items():
            assert params[column] == expected, column

    def test_carries_no_id(self):
        # Prod assigns its own; local ids would collide with rows already there.
        assert "id" not in detection_to_record(DETECTION_ROW)

    def test_preserves_the_fusion_verdict(self):
        # The entire point of the transfer: fusion is never recomputed on read.
        params = record_to_detection_params(detection_to_record(DETECTION_ROW))
        assert params["match_state"] == "dark"
        assert params["is_dark"] is True
        assert params["dark_margin_m"] == 315.25

    def test_matched_detection_keeps_its_mmsi(self):
        row = DETECTION_ROW | {
            "match_state": "matched",
            "is_dark": False,
            "matched_mmsi": 265547000,
            "candidate_mmsi": None,
            "match_distance_m": 142.5,
        }
        params = record_to_detection_params(detection_to_record(row))
        assert params["matched_mmsi"] == 265547000
        assert params["match_distance_m"] == 142.5

    def test_land_detection_keeps_null_match_state(self):
        row = DETECTION_ROW | {"on_land": True, "match_state": None, "is_dark": None}
        params = record_to_detection_params(detection_to_record(row))
        assert params["on_land"] is True
        assert params["match_state"] is None
        assert params["is_dark"] is None


class TestShipRoundTrip:
    def test_restores_every_column(self):
        params = record_to_ship_params(ship_to_record(SHIP_ROW))
        for column, expected in SHIP_ROW.items():
            assert params[column] == expected, column

    def test_null_name_survives(self):
        row = SHIP_ROW | {"ship_name": None, "callsign": None, "ship_type": None}
        params = record_to_ship_params(ship_to_record(row))
        assert params["ship_name"] is None
        assert params["callsign"] is None
        assert params["ship_type"] is None


class TestStream:
    def test_round_trips_through_gzip(self):
        records = [
            meta(scenes=1, detections=1, ships=1),
            scene_to_record(SCENE_ROW),
            detection_to_record(DETECTION_ROW),
            ship_to_record(SHIP_ROW),
        ]
        assert decode_stream(encode_stream(records)) == records

    def test_encoding_is_deterministic(self):
        # mtime is pinned, so export → import → re-export can be diffed byte-wise.
        records = [meta(), scene_to_record(SCENE_ROW)]
        assert encode_stream(records) == encode_stream(records)

    def test_rejects_unknown_version(self):
        with pytest.raises(ValueError, match="unsupported format version"):
            validate([meta() | {"version": 99}])

    def test_rejects_stream_without_meta(self):
        with pytest.raises(ValueError, match="meta record"):
            validate([scene_to_record(SCENE_ROW)])

    def test_rejects_detection_orphaned_from_its_scene(self):
        orphan = detection_to_record(DETECTION_ROW | {"scene_id": "no-such-scene"})
        with pytest.raises(ValueError, match="not in the stream"):
            validate([meta(), scene_to_record(SCENE_ROW), orphan])

    def test_accepts_detection_matching_its_scene(self):
        records = [meta(), scene_to_record(SCENE_ROW), detection_to_record(DETECTION_ROW)]
        assert validate(records) == records

    def test_rejects_record_missing_kind(self):
        # A concatenated or hand-edited stream should fail the same way as
        # every other malformed-stream case: ValueError, not a bare KeyError.
        records = [meta(), scene_to_record(SCENE_ROW)]
        del records[1]["kind"]
        with pytest.raises(ValueError, match="kind"):
            validate(records)


class TestReadStream:
    def test_base64_matches_raw_gzip(self):
        blob = encode_stream([meta(), scene_to_record(SCENE_ROW)])
        assert read_stream(base64.b64encode(blob)) == read_stream(blob)

    def test_base64_with_embedded_newlines(self):
        # /usr/bin/base64 wraps output at 76 columns; b64decode must discard
        # those newlines rather than choke on them.
        blob = encode_stream([meta(), scene_to_record(SCENE_ROW)])
        encoded = base64.b64encode(blob)
        wrapped = b"\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76)) + b"\n"
        assert read_stream(wrapped) == read_stream(blob)


class TestColumnsMatchModels:
    """The column tuples are hand-maintained and are the wire format's single
    source of truth. No Alembic, no linter, no CI means a column added to
    app.models would otherwise be silently dropped by export with no test
    failing."""

    def test_scene_columns_match_model(self):
        model_columns = set(SarSceneRow.__table__.columns.keys())
        assert set(SCENE_COLUMNS) == model_columns - {"footprint", "overview_png"}

    def test_detection_columns_match_model(self):
        model_columns = set(SarDetection.__table__.columns.keys())
        assert set(DETECTION_COLUMNS) == model_columns - {"id", "location"}

    def test_ship_columns_match_model(self):
        model_columns = set(ShipMetadata.__table__.columns.keys())
        assert set(SHIP_COLUMNS) == model_columns
