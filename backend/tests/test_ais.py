from datetime import datetime, timezone

import pytest

from app.ais import (
    build_subscribe_message,
    nav_status_label,
    parse_class_b_position_report,
    parse_class_b_static_data,
    parse_position_report,
    parse_ship_static_data,
)
from app.rois import get_roi


SAMPLE_POSITION_REPORT = {
    "MessageType": "PositionReport",
    "MetaData": {
        "MMSI": 477123456,
        "ShipName": "TEST VESSEL",
        "latitude": 22.291,
        "longitude": 114.184,
        "time_utc": "2024-08-30 13:24:32.987532323 +0000 UTC",
    },
    "Message": {
        "PositionReport": {
            "UserID": 477123456,
            "Sog": 12.7,
            "Cog": 271.3,
            "TrueHeading": 270,
            "NavigationalStatus": 0,
            "Latitude": 22.291,
            "Longitude": 114.184,
        }
    },
}


def test_parses_full_position_report():
    pos = parse_position_report(SAMPLE_POSITION_REPORT)
    assert pos is not None
    assert pos.mmsi == 477123456
    assert (pos.lat, pos.lon) == (22.291, 114.184)
    assert pos.sog == 12.7
    assert pos.cog == 271.3
    assert pos.true_heading == 270
    assert pos.nav_status == 0
    # AISStream timestamps are nanosecond-precision Go strings; we truncate to micros.
    assert pos.time == datetime(2024, 8, 30, 13, 24, 32, 987532, tzinfo=timezone.utc)


def test_non_position_report_returns_none():
    msg = {**SAMPLE_POSITION_REPORT, "MessageType": "ShipStaticData"}
    assert parse_position_report(msg) is None


@pytest.mark.parametrize("missing_field", ["latitude", "longitude", "time_utc"])
def test_missing_required_field_returns_none(missing_field: str):
    meta = {**SAMPLE_POSITION_REPORT["MetaData"], missing_field: None}
    msg = {**SAMPLE_POSITION_REPORT, "MetaData": meta}
    assert parse_position_report(msg) is None


def test_mmsi_falls_back_to_inner_user_id():
    """If MetaData.MMSI is missing, fall back to Message.PositionReport.UserID."""
    meta = {**SAMPLE_POSITION_REPORT["MetaData"]}
    del meta["MMSI"]
    msg = {**SAMPLE_POSITION_REPORT, "MetaData": meta}
    pos = parse_position_report(msg)
    assert pos is not None
    assert pos.mmsi == 477123456


def test_mmsi_missing_everywhere_returns_none():
    meta = {**SAMPLE_POSITION_REPORT["MetaData"], "MMSI": None}
    inner = {**SAMPLE_POSITION_REPORT["Message"]["PositionReport"]}
    inner.pop("UserID", None)
    msg = {**SAMPLE_POSITION_REPORT, "MetaData": meta, "Message": {"PositionReport": inner}}
    assert parse_position_report(msg) is None


def test_unparseable_time_returns_none():
    meta = {**SAMPLE_POSITION_REPORT["MetaData"], "time_utc": "not a timestamp"}
    msg = {**SAMPLE_POSITION_REPORT, "MetaData": meta}
    assert parse_position_report(msg) is None


def test_optional_fields_absent_yields_none_values():
    msg = {**SAMPLE_POSITION_REPORT, "Message": {"PositionReport": {"UserID": 1}}}
    pos = parse_position_report(msg)
    assert pos is not None
    assert pos.sog is None
    assert pos.cog is None
    assert pos.true_heading is None
    assert pos.nav_status is None


# ── Class B position report parser ─────────────────────────────────────────

SAMPLE_CLASS_B_STANDARD = {
    "MessageType": "StandardClassBPositionReport",
    "MetaData": {
        "MMSI": 244770688,
        "ShipName": "",
        "latitude": 51.85,
        "longitude": 4.25,
        "time_utc": "2024-08-30 13:24:32.000000000 +0000 UTC",
    },
    "Message": {
        "StandardClassBPositionReport": {
            "UserID": 244770688,
            "Sog": 5.4,
            "Cog": 88.2,
            "TrueHeading": 90,
            "Latitude": 51.85,
            "Longitude": 4.25,
        }
    },
}

SAMPLE_CLASS_B_EXTENDED = {
    "MessageType": "ExtendedClassBPositionReport",
    "MetaData": {
        "MMSI": 244770689,
        "ShipName": "LITTLE SKIFF",
        "latitude": 51.86,
        "longitude": 4.26,
        "time_utc": "2024-08-30 13:24:33.000000000 +0000 UTC",
    },
    "Message": {
        "ExtendedClassBPositionReport": {
            "UserID": 244770689,
            "Sog": 3.1,
            "Cog": 200.0,
            "TrueHeading": 199,
            "Name": "LITTLE SKIFF",
            "Type": 37,
        }
    },
}


def test_parses_standard_class_b_position_report():
    pos = parse_class_b_position_report(SAMPLE_CLASS_B_STANDARD)
    assert pos is not None
    assert pos.mmsi == 244770688
    assert (pos.lat, pos.lon) == (51.85, 4.25)
    assert pos.sog == 5.4
    assert pos.cog == 88.2
    assert pos.true_heading == 90
    assert pos.nav_status is None  # Class B never reports navigational status


def test_parses_extended_class_b_position_report():
    pos = parse_class_b_position_report(SAMPLE_CLASS_B_EXTENDED)
    assert pos is not None
    assert pos.mmsi == 244770689
    assert pos.sog == 3.1
    assert pos.cog == 200.0
    assert pos.nav_status is None


def test_class_b_position_ignores_other_message_types():
    assert parse_class_b_position_report(SAMPLE_POSITION_REPORT) is None


def test_class_b_position_missing_required_field_returns_none():
    meta = {**SAMPLE_CLASS_B_STANDARD["MetaData"], "latitude": None}
    msg = {**SAMPLE_CLASS_B_STANDARD, "MetaData": meta}
    assert parse_class_b_position_report(msg) is None


def test_subscribe_message_uses_aisstream_corner_order():
    """AISStream wants [[SW lat, SW lon], [NE lat, NE lon]] — lat-first, GeoJSON-flipped."""
    sub = build_subscribe_message("KEY123", [get_roi("singapore_strait")])
    assert sub == {
        "APIKey": "KEY123",
        "BoundingBoxes": [[[0.95, 103.45], [1.40, 104.20]]],
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }


def test_subscribe_message_includes_all_rois():
    """Multiple ROIs produce multiple bounding boxes in the same subscription."""
    from app.rois import ROIS

    sub = build_subscribe_message("KEY123", ROIS.values())
    assert sub["APIKey"] == "KEY123"
    assert sub["FilterMessageTypes"] == ["PositionReport", "ShipStaticData"]
    assert len(sub["BoundingBoxes"]) == len(ROIS)
    # Each box is [[lat, lon], [lat, lon]] — verify lat/lon order for one entry
    singapore = next(b for b in sub["BoundingBoxes"] if b == [[0.95, 103.45], [1.40, 104.20]])
    assert singapore is not None


# ── ShipStaticData parser ──────────────────────────────────────────────────

SAMPLE_SHIP_STATIC = {
    "MessageType": "ShipStaticData",
    "MetaData": {
        "MMSI": 477996333,
        "ShipName": "SEA SPARKLE",
        "time_utc": "2024-08-30 13:24:32.000000000 +0000 UTC",
    },
    "Message": {
        "ShipStaticData": {
            "UserID": 477996333,
            "Name": "SEA SPARKLE",
            "CallSign": "VRS5586",
            "Type": 40,
        }
    },
}


def test_parses_ship_static_data():
    meta = parse_ship_static_data(SAMPLE_SHIP_STATIC)
    assert meta is not None
    assert meta.mmsi == 477996333
    assert meta.ship_name == "SEA SPARKLE"
    assert meta.ship_type == 40
    assert meta.callsign == "VRS5586"
    assert meta.time == datetime(2024, 8, 30, 13, 24, 32, 0, tzinfo=timezone.utc)


def test_non_ship_static_returns_none():
    msg = {**SAMPLE_SHIP_STATIC, "MessageType": "PositionReport"}
    assert parse_ship_static_data(msg) is None


def test_ship_static_missing_mmsi_returns_none():
    meta = {**SAMPLE_SHIP_STATIC["MetaData"], "MMSI": None}
    inner = {**SAMPLE_SHIP_STATIC["Message"]["ShipStaticData"]}
    inner.pop("UserID", None)
    msg = {**SAMPLE_SHIP_STATIC, "MetaData": meta, "Message": {"ShipStaticData": inner}}
    assert parse_ship_static_data(msg) is None


def test_ship_static_name_stripped():
    inner = {**SAMPLE_SHIP_STATIC["Message"]["ShipStaticData"], "Name": "  PADDED  "}
    msg = {**SAMPLE_SHIP_STATIC, "Message": {"ShipStaticData": inner}}
    meta = parse_ship_static_data(msg)
    assert meta is not None
    assert meta.ship_name == "PADDED"


def test_ship_static_optional_fields_absent():
    # Clear both Name sources so there's truly no name to fall back to.
    stripped_meta = {**SAMPLE_SHIP_STATIC["MetaData"]}
    stripped_meta.pop("ShipName", None)
    inner = {"UserID": 477996333}
    msg = {**SAMPLE_SHIP_STATIC, "MetaData": stripped_meta, "Message": {"ShipStaticData": inner}}
    meta = parse_ship_static_data(msg)
    assert meta is not None
    assert meta.ship_name is None
    assert meta.ship_type is None
    assert meta.callsign is None


def test_ship_static_name_falls_back_to_metadata():
    """If Message.ShipStaticData.Name is absent, use MetaData.ShipName."""
    inner = {**SAMPLE_SHIP_STATIC["Message"]["ShipStaticData"]}
    inner.pop("Name", None)
    msg = {**SAMPLE_SHIP_STATIC, "Message": {"ShipStaticData": inner}}
    meta = parse_ship_static_data(msg)
    assert meta is not None
    assert meta.ship_name == "SEA SPARKLE"
