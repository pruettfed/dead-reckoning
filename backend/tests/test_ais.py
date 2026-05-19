from datetime import datetime, timezone

import pytest

from app.ais import build_subscribe_message, parse_position_report, parse_ship_static_data
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


def test_subscribe_message_uses_aisstream_corner_order():
    """AISStream wants [[SW lat, SW lon], [NE lat, NE lon]] — lat-first, GeoJSON-flipped."""
    sub = build_subscribe_message("KEY123", [get_roi("south_china_sea")])
    assert sub == {
        "APIKey": "KEY123",
        "BoundingBoxes": [[[0.0, 105.0], [23.0, 122.0]]],
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
    scs = next(b for b in sub["BoundingBoxes"] if b == [[0.0, 105.0], [23.0, 122.0]])
    assert scs is not None


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
