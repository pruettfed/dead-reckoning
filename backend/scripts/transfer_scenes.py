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

    # scene ids are CDSE product UUIDs and collide across machines: a re-import
    # that would replace detections already in the target aborts unless told
    # the replacement is intended
    base64 < backend/export.jsonl.gz \
        | railway ssh -s web -- python scripts/transfer_scenes.py import --force

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
    if any("kind" not in r for r in records):
        raise ValueError("record missing 'kind'")
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
    ORDER BY roi, sensed_at, id
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


SCENE_UPSERT = """
    INSERT INTO sar_scenes (
        id, name, roi, sensed_at, footprint, platform, status, processed_at,
        error, imaged_bbox, overview_png, chance_match_rate,
        recall_large_total, recall_large_detected
    ) VALUES (
        :id, :name, :roi, :sensed_at, ST_GeogFromText(:footprint_ewkt), :platform,
        :status, :processed_at, :error, :imaged_bbox, :overview_png,
        :chance_match_rate, :recall_large_total, :recall_large_detected
    )
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        roi = EXCLUDED.roi,
        sensed_at = EXCLUDED.sensed_at,
        footprint = EXCLUDED.footprint,
        platform = EXCLUDED.platform,
        status = EXCLUDED.status,
        processed_at = EXCLUDED.processed_at,
        error = EXCLUDED.error,
        imaged_bbox = EXCLUDED.imaged_bbox,
        overview_png = EXCLUDED.overview_png,
        chance_match_rate = EXCLUDED.chance_match_rate,
        recall_large_total = EXCLUDED.recall_large_total,
        recall_large_detected = EXCLUDED.recall_large_detected
"""

# Delete-then-insert rather than upsert: sar_detections.id is an autoincrement
# bigint and the target already holds rows in the same range, so carrying local
# ids across would collide. Letting the target's sequence assign makes a
# re-import converge instead of duplicating.
DETECTION_DELETE = "DELETE FROM sar_detections WHERE scene_id = ANY(:scene_ids)"

DETECTION_INSERT = """
    INSERT INTO sar_detections (
        scene_id, location, confidence, confidence_bucket, match_state, is_dark,
        matched_mmsi, candidate_mmsi, match_distance_m, match_time_delta_s,
        dark_margin_m, on_land
    ) VALUES (
        :scene_id, ST_GeogFromText(:location_ewkt), :confidence, :confidence_bucket,
        :match_state, :is_dark, :matched_mmsi, :candidate_mmsi, :match_distance_m,
        :match_time_delta_s, :dark_margin_m, :on_land
    )
"""

# Guarded on last_updated: if AIS resumes, live metadata must not be clobbered
# by an older import.
SHIP_UPSERT = """
    INSERT INTO ship_metadata (mmsi, ship_name, ship_type, callsign, last_updated)
    VALUES (:mmsi, :ship_name, :ship_type, :callsign, :last_updated)
    ON CONFLICT (mmsi) DO UPDATE SET
        ship_name = EXCLUDED.ship_name,
        ship_type = EXCLUDED.ship_type,
        callsign = EXCLUDED.callsign,
        last_updated = EXCLUDED.last_updated
    WHERE ship_metadata.last_updated < EXCLUDED.last_updated
"""

GZIP_MAGIC = b"\x1f\x8b"


def read_stream(raw: bytes) -> list[dict]:
    """Accept either raw gzip or the base64 wrapping the ssh pipe needs."""
    blob = raw if raw[:2] == GZIP_MAGIC else base64.b64decode(raw)
    return decode_stream(blob)


async def _run_import(args: argparse.Namespace) -> int:
    from sqlalchemy import text  # noqa: PLC0415

    from app.database import SessionLocal  # noqa: PLC0415

    records = read_stream(sys.stdin.buffer.read())
    scenes = [r for r in records if r["kind"] == "scene"]
    detections = [r for r in records if r["kind"] == "detection"]
    ships = [r for r in records if r["kind"] == "ship"]
    scene_ids = [r["id"] for r in scenes]

    async with SessionLocal() as session:
        for record in scenes:
            await session.execute(text(SCENE_UPSERT), record_to_scene_params(record))
        removed = await session.execute(text(DETECTION_DELETE), {"scene_ids": scene_ids})
        replacing = removed.rowcount

        # Reported before the commit, while it is still reversible: scene ids
        # are CDSE product UUIDs and collide across machines, so a replay of an
        # import already applied elsewhere destroys the target's own detections
        # for those scenes before overwriting them with the local copy.
        if replacing:
            print(
                f"replacing {replacing} existing detection(s) already in the "
                f"target database for scene(s) {', '.join(scene_ids)}",
                file=sys.stderr,
            )
            if not args.dry_run and not args.force:
                await session.rollback()
                print(
                    "aborting: re-run with --force to confirm the replacement, "
                    "or --dry-run to preview it safely",
                    file=sys.stderr,
                )
                return 2

        if detections:
            await session.execute(
                text(DETECTION_INSERT),
                [record_to_detection_params(r) for r in detections],
            )
        for record in ships:
            await session.execute(text(SHIP_UPSERT), record_to_ship_params(record))

        # Settle the transaction before reporting: a message saying "wrote" must
        # not precede the commit that could still fail.
        if args.dry_run:
            await session.rollback()
        else:
            await session.commit()

    verb = "would write" if args.dry_run else "wrote"
    print(
        f"{verb} {len(scenes)} scenes, {len(detections)} detections "
        f"(replacing {replacing}), {len(ships)} ship_metadata rows"
    )
    for record in scenes:
        print(f"  {record['roi']:<20} {record['sensed_at'][:10]}  {record['id']}")
    if args.dry_run:
        print("dry run — rolled back")
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

    importer = subparsers.add_parser("import", help="read a stream on stdin and write it")
    importer.add_argument("--dry-run", action="store_true", help="report, then roll back")
    importer.add_argument(
        "--force",
        action="store_true",
        help="confirm replacing detections already in the target for these scene ids",
    )
    importer.set_defaults(func=_run_import)

    args = parser.parse_args()
    return await args.func(args)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
