import re

import pytest

from app.flags import MID_COUNTRIES, flag_for_mmsi


@pytest.mark.parametrize(
    ("mmsi", "iso2", "country"),
    [
        (416000123, "TW", "Taiwan"),          # north_taiwan
        (412345678, "CN", "China"),
        (422123456, "IR", "Iran"),            # hormuz_strait / kharg_island
        (271123456, "TR", "Turkey"),          # bosphorus_marmara
        (273123456, "RU", "Russian Federation"),  # kerch_strait
        (468123456, "SY", "Syrian Arab Republic"),  # syria_coast_sts
        (230123456, "FI", "Finland"),         # gulf_of_finland
        (248123456, "MT", "Malta"),           # malta_hurds_bank
        (219123456, "DK", "Denmark"),         # skagen_kattegat
        (353136000, "PA", "Panama"),          # flags of convenience below
        (636123456, "LR", "Liberia"),
        (538123456, "MH", "Marshall Islands"),
        (613123456, "CM", "Cameroon"),
        (626123456, "GA", "Gabon"),
    ],
)
def test_resolves_ship_station_mmsi(mmsi, iso2, country):
    flag = flag_for_mmsi(mmsi)
    assert flag is not None
    assert (flag.iso2, flag.country) == (iso2, country)


@pytest.mark.parametrize(
    ("mmsi", "what"),
    [
        (982320123, "craft associated with a parent ship (98MID)"),
        (992476010, "aid to navigation (99MID)"),
        (111232500, "SAR aircraft (111MID)"),
        (823201234, "handheld VHF (8MID)"),
        (970123456, "AIS-SART"),
        (972123456, "MOB beacon"),
        (974123456, "EPIRB-AIS"),
    ],
)
def test_non_ship_stations_are_unflagged(mmsi, what):
    """Every non-ship-station prefix falls outside the assigned 201-775 MID
    range, so the bare table lookup fails closed with no prefix dispatch."""
    assert flag_for_mmsi(mmsi) is None, what


def test_leading_zero_stripped_mmsi_is_unflagged():
    """Regression guard: `mmsi` is a BigInteger, so coast station 002320123 is
    stored as 2320123. Its first three digits are "232" -- a valid MID (United
    Kingdom) -- so without the nine-digit check this would confidently flag a
    shore installation as a British vessel."""
    assert MID_COUNTRIES["232"].iso2 == "GB"  # the trap this guards against
    assert flag_for_mmsi(2320123) is None  # 00MID coast station
    assert flag_for_mmsi(23201234) is None  # 0MID group of ships


def test_unassigned_mid_is_unflagged():
    unassigned = next(
        str(m) for m in range(201, 776) if str(m) not in MID_COUNTRIES
    )
    assert flag_for_mmsi(int(unassigned + "123456")) is None


def test_table_is_well_formed():
    assert len(MID_COUNTRIES) == 292
    for mid, flag in MID_COUNTRIES.items():
        assert re.fullmatch(r"\d{3}", mid), mid
        assert 201 <= int(mid) <= 775, mid
        assert re.fullmatch(r"[A-Z]{2}", flag.iso2), (mid, flag.iso2)
        assert flag.country.strip() == flag.country and flag.country, mid
