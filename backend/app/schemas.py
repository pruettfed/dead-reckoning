"""Response models for the public API.

Every read endpoint returns hand-built dicts from a raw SQL SELECT, so a field
reached the client purely because the query asked for it — how `sar_scenes.error`
came to be served raw. These models make the response surface a declaration:
FastAPI filters every response through them, dropping unknown keys rather than
erroring on a schema change.

Field names match the SQL aliases; frontend/src/types.ts mirrors these.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SourceState(Base):
    state: str
    last_message_at: datetime | None = None
    lag_seconds: float | None = None
    connected_since: datetime | None = None
    reconnect_count: int = 0
    error_count: int = 0
    # Withheld in production — see sources.snapshot. Optional here so the same
    # model serves both environments.
    last_error: str | None = None


class Health(Base):
    status: str
    database: str
    version: str
    sources: dict[str, SourceState]


class StatusMessage(Base):
    active: bool
    message: str | None
    level: str
    title: str | None = None
    updated_at: datetime | None = None


class Roi(Base):
    name: str
    label: str
    blurb: str
    passes_per_month: int
    ais_bbox: list[float]
    sar_bbox: list[float]
    mode: str


class VesselCount(Base):
    count: int


class Vessel(Base):
    mmsi: int
    time: datetime
    lat: float
    lon: float
    sog: float | None = None
    cog: float | None = None
    nav_status: int | None = None
    ship_name: str | None = None
    ship_type: int | None = None
    callsign: str | None = None
    flag_iso2: str | None = None
    flag_country: str | None = None


class TrackPoint(Base):
    time: datetime
    lat: float
    lon: float
    sog: float | None = None
    cog: float | None = None
    ship_name: str | None = None
    ship_type: int | None = None
    callsign: str | None = None
    flag_iso2: str | None = None
    flag_country: str | None = None


class Sighting(Base):
    detection_id: int
    scene_id: str
    roi: str
    label: str
    sensed_at: datetime
    match_state: str | None = None
    is_dark: bool | None = None
    confidence: float
    matched: bool


class Scene(Base):
    id: str
    name: str
    roi: str
    sensed_at: datetime
    platform: str | None = None
    status: str
    processed_at: datetime | None = None
    # Classified from the raw exception; the raw text is never served. The
    # absence of an `error` field here is the point of this model.
    failure_reason: str | None = None
    footprint: dict | None = None
    imaged_bbox: list[float] | None = None
    has_overview: bool = False
    chance_match_rate: float | None = None
    recall_large_total: int | None = None
    recall_large_detected: int | None = None
    detection_count: int = 0
    dark_count: int = 0
    indeterminate_count: int = 0
    land_count: int = 0


class Detection(Base):
    id: int
    lat: float
    lon: float
    confidence: float
    confidence_bucket: str
    is_dark: bool | None = None
    match_state: str | None = None
    on_land: bool = False
    matched_mmsi: int | None = None
    match_distance_m: float | None = None
    match_time_delta_s: float | None = None
    dark_margin_m: float | None = None
    candidate_mmsi: int | None = None
    ship_name: str | None = None
    ship_type: int | None = None
    callsign: str | None = None
    candidate_name: str | None = None
    flag_iso2: str | None = None
    flag_country: str | None = None


class NextPass(Base):
    latest_scene_sensed_at: datetime | None = None
    next_expected_at: datetime | None = None
    last_processed_at: datetime | None = None


class SchedulerStatus(Base):
    state: str
    detail: str


class ScheduleRow(Base):
    name: str
    label: str
    mode: str
    latest_scene_sensed_at: datetime | None = None
    next_expected_at: datetime | None = None
    last_processed_at: datetime | None = None
    state: str


class MostRecentAnalysis(Base):
    roi: str
    label: str
    mode: str
    sensed_at: datetime
    processed_at: datetime | None = None
    detection_count: int = 0
    dark_count: int = 0


class Schedule(Base):
    scheduler: SchedulerStatus
    regions: list[ScheduleRow]
    most_recent: MostRecentAnalysis | None = None
    month_to_date_pu: float
    pu_monthly_ceiling: float
