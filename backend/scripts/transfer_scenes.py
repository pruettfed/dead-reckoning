"""Move fused SAR scenes between databases.

Production has never received AIS — AISStream has been silent since 2026-08-05 —
so `warmup_gate` holds its fused ROIs indefinitely and they can only be filled
from a machine that fused them while AIS was live. This copies whole scenes:
the detections carry their stored `match_state`/`matched_mmsi`/`dark_margin_m`,
which fusion never recomputes on read.

    # where the fused scenes live
    cd backend
    .venv/bin/python scripts/transfer_scenes.py export \
        --roi north_taiwan --roi gulf_of_finland --roi skagen_kattegat > export.jsonl.gz

    # into production, from the repo root so `railway` finds its project link
    base64 < backend/export.jsonl.gz \
        | railway ssh -s web -- python scripts/transfer_scenes.py import --dry-run
    base64 < backend/export.jsonl.gz \
        | railway ssh -s web -- python scripts/transfer_scenes.py import

AIS positions are deliberately not transferred: AIS_RETENTION_DAYS pruned them
and none survive on either side. A matched detection therefore renders with no
AIS position beside it. `pu_ledger` is deliberately not transferred either — it
is production's record of its own scheduler's spend, and scheduler.decide()
gates on it.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import gzip
import io
import json
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FORMAT_VERSION = 1

# Columns carried verbatim. The geography and bytea columns are handled
# separately (EWKT and base64), and sar_detections.id is deliberately absent —
# see record_to_detection_params' callers in the import path.
SCENE_COLUMNS = (
    "id",
    "name",
    "roi",
    "sensed_at",
    "platform",
    "status",
    "processed_at",
    "error",
    "imaged_bbox",
    "chance_match_rate",
    "recall_large_total",
    "recall_large_detected",
)
DETECTION_COLUMNS = (
    "scene_id",
    "confidence",
    "confidence_bucket",
    "match_state",
    "is_dark",
    "matched_mmsi",
    "candidate_mmsi",
    "match_distance_m",
    "match_time_delta_s",
    "dark_margin_m",
    "on_land",
)
SHIP_COLUMNS = ("mmsi", "ship_name", "ship_type", "callsign", "last_updated")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def scene_to_record(row: Mapping[str, Any]) -> dict:
    """A `sar_scenes` row, selected with ST_AsEWKT(footprint) AS footprint_ewkt."""
    record: dict[str, Any] = {"kind": "scene"}
    record.update({column: row[column] for column in SCENE_COLUMNS})
    record["sensed_at"] = _iso(row["sensed_at"])
    record["processed_at"] = _iso(row["processed_at"])
    record["footprint_ewkt"] = row["footprint_ewkt"]
    png = row["overview_png"]
    record["overview_png_b64"] = (
        base64.b64encode(bytes(png)).decode("ascii") if png is not None else None
    )
    return record


def record_to_scene_params(record: Mapping[str, Any]) -> dict:
    params = {column: record[column] for column in SCENE_COLUMNS}
    params["sensed_at"] = _dt(record["sensed_at"])
    params["processed_at"] = _dt(record["processed_at"])
    params["footprint_ewkt"] = record["footprint_ewkt"]
    encoded = record["overview_png_b64"]
    params["overview_png"] = base64.b64decode(encoded) if encoded is not None else None
    return params


def detection_to_record(row: Mapping[str, Any]) -> dict:
    """A `sar_detections` row, selected with ST_AsEWKT(location) AS location_ewkt.

    Carries no `id`: production's sequence assigns one on insert, because local
    ids would collide with the rows already there.
    """
    record: dict[str, Any] = {"kind": "detection"}
    record.update({column: row[column] for column in DETECTION_COLUMNS})
    record["location_ewkt"] = row["location_ewkt"]
    return record


def record_to_detection_params(record: Mapping[str, Any]) -> dict:
    params = {column: record[column] for column in DETECTION_COLUMNS}
    params["location_ewkt"] = record["location_ewkt"]
    return params


def ship_to_record(row: Mapping[str, Any]) -> dict:
    record: dict[str, Any] = {"kind": "ship"}
    record.update({column: row[column] for column in SHIP_COLUMNS})
    record["last_updated"] = _iso(row["last_updated"])
    return record


def record_to_ship_params(record: Mapping[str, Any]) -> dict:
    params = {column: record[column] for column in SHIP_COLUMNS}
    params["last_updated"] = _dt(record["last_updated"])
    return params


def encode_stream(records: Iterable[Mapping[str, Any]]) -> bytes:
    """Gzipped JSONL. mtime is pinned so identical records encode identically."""
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as archive:
        for record in records:
            line = json.dumps(record, separators=(",", ":"), sort_keys=True)
            archive.write(f"{line}\n".encode())
    return buffer.getvalue()


def decode_stream(blob: bytes) -> list[dict]:
    with gzip.GzipFile(fileobj=io.BytesIO(blob), mode="rb") as archive:
        text = archive.read().decode()
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    return validate(records)


def validate(records: list[dict]) -> list[dict]:
    """Reject a stream this version cannot faithfully import."""
    if not records or records[0].get("kind") != "meta":
        raise ValueError("stream does not begin with a meta record")
    version = records[0].get("version")
    if version != FORMAT_VERSION:
        raise ValueError(
            f"unsupported format version {version!r}, expected {FORMAT_VERSION}"
        )
    scene_ids = {r["id"] for r in records if r["kind"] == "scene"}
    referenced = {r["scene_id"] for r in records if r["kind"] == "detection"}
    orphans = sorted(referenced - scene_ids)
    if orphans:
        raise ValueError(f"detections reference scenes not in the stream: {orphans}")
    return records


SCENE_SELECT = """
    SELECT id, name, roi, sensed_at, platform, status, processed_at, error,
           imaged_bbox, chance_match_rate, recall_large_total, recall_large_detected,
           ST_AsEWKT(footprint) AS footprint_ewkt,
           overview_png
    FROM sar_scenes
    WHERE roi = ANY(:rois) AND status = 'processed'
    ORDER BY roi, sensed_at
"""

DETECTION_SELECT = """
    SELECT scene_id, confidence, confidence_bucket, match_state, is_dark,
           matched_mmsi, candidate_mmsi, match_distance_m, match_time_delta_s,
           dark_margin_m, on_land,
           ST_AsEWKT(location) AS location_ewkt
    FROM sar_detections
    WHERE scene_id = ANY(:scene_ids)
    ORDER BY scene_id, id
"""

# Both match columns, because /api/scenes/{id}/detections joins ship_metadata on
# each: a candidate's name is what makes an indeterminate contact readable.
SHIP_SELECT = """
    SELECT mmsi, ship_name, ship_type, callsign, last_updated
    FROM ship_metadata
    WHERE mmsi IN (
        SELECT matched_mmsi FROM sar_detections
        WHERE scene_id = ANY(:scene_ids) AND matched_mmsi IS NOT NULL
        UNION
        SELECT candidate_mmsi FROM sar_detections
        WHERE scene_id = ANY(:scene_ids) AND candidate_mmsi IS NOT NULL
    )
    ORDER BY mmsi
"""


async def _run_export(args: argparse.Namespace) -> int:
    from sqlalchemy import text  # noqa: PLC0415

    from app.database import SessionLocal  # noqa: PLC0415

    async with SessionLocal() as session:
        scenes = (
            await session.execute(text(SCENE_SELECT), {"rois": args.rois})
        ).mappings().all()
        if not scenes:
            print(f"no processed scenes for {args.rois}", file=sys.stderr)
            return 1
        scene_ids = [row["id"] for row in scenes]
        detections = (
            await session.execute(text(DETECTION_SELECT), {"scene_ids": scene_ids})
        ).mappings().all()
        ships = (
            await session.execute(text(SHIP_SELECT), {"scene_ids": scene_ids})
        ).mappings().all()

    records = [
        {
            "kind": "meta",
            "version": FORMAT_VERSION,
            "exported_at": datetime.now(tz=timezone.utc).isoformat(),
            "counts": {
                "scenes": len(scenes),
                "detections": len(detections),
                "ships": len(ships),
            },
        }
    ]
    records.extend(scene_to_record(row) for row in scenes)
    records.extend(detection_to_record(row) for row in detections)
    records.extend(ship_to_record(row) for row in ships)

    blob = encode_stream(validate(records))
    sys.stdout.buffer.write(blob)
    sys.stdout.buffer.flush()
    for row in scenes:
        print(f"  {row['roi']:<20} {row['sensed_at']:%Y-%m-%d}  {row['id']}", file=sys.stderr)
    print(
        f"exported {len(scenes)} scenes, {len(detections)} detections, "
        f"{len(ships)} ship_metadata rows ({len(blob) / 1e6:.1f} MB gzipped)",
        file=sys.stderr,
    )
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="write processed scenes to stdout")
    export.add_argument(
        "--roi",
        action="append",
        dest="rois",
        required=True,
        metavar="ROI",
        help="ROI to export processed scenes for; repeatable",
    )
    export.set_defaults(func=_run_export)

    args = parser.parse_args()
    return await args.func(args)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
