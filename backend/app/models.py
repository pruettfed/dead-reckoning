from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

__all__ = [
    "Base",
    "AISPosition",
    "ShipMetadata",
    "SarSceneRow",
    "SarDetection",
    "LandPolygon",
]


class AISPosition(Base):
    __tablename__ = "ais_positions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mmsi: Mapped[int] = mapped_column(BigInteger, nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[str] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=True),
        nullable=False,
    )
    sog: Mapped[float | None] = mapped_column(Float, nullable=True)
    cog: Mapped[float | None] = mapped_column(Float, nullable=True)
    true_heading: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    nav_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        Index("ix_ais_positions_mmsi_time", "mmsi", "time"),
    )


class ShipMetadata(Base):
    """Latest static info per vessel, upserted on each AIS ShipStaticData message.
    Ship type is stored as the raw AIS numeric code"""

    __tablename__ = "ship_metadata"

    mmsi: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    ship_name: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ship_type: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    callsign: Mapped[str | None] = mapped_column(String(7), nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SarSceneRow(Base):
    """One Sentinel-1 acquisition analyzed (or being analyzed) for an ROI."""

    __tablename__ = "sar_scenes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # CDSE product UUID
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    roi: Mapped[str] = mapped_column(String(32), nullable=False)
    sensed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # GEOMETRY (not POLYGON): CDSE footprints are occasionally MULTIPOLYGON
    footprint: Mapped[str] = mapped_column(
        Geography(geometry_type="GEOMETRY", srid=4326, spatial_index=True),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # processing | processed | failed
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The rectangle pixels were actually fetched for — the ROI's sar_bbox at the
    # time of analysis, as (min_lon, min_lat, max_lon, max_lat). Stored rather
    # than looked up because `footprint` is the full ~250 km swath, not the
    # imaged area, and because a retuned sar_bbox would misplace old overlays.
    imaged_bbox: Mapped[list[float] | None] = mapped_column(ARRAY(Float), nullable=True)
    # Downsampled grayscale PNG of the chip, for map display and eyeballing
    # detections against the radar returns.
    overview_png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    __table_args__ = (
        Index("ix_sar_scenes_roi_sensed_at", "roi", "sensed_at"),
    )


class SarDetection(Base):
    """One YOLO ship detection in a SAR scene; fusion fills the match columns."""

    __tablename__ = "sar_detections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scene_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("sar_scenes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    location: Mapped[str] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=True),
        nullable=False,
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_bucket: Mapped[str] = mapped_column(String(8), nullable=False)
    is_dark: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    matched_mmsi: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    match_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_time_delta_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Detection fell inside land_polygons (a rock, breakwater or shore
    # structure, not a vessel). Excluded from fusion and from the default API
    # response. Recomputable from `location` alone at zero PU — see landmask.py.
    on_land: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class LandPolygon(Base):
    """Coastline geometry, clipped to the ROI sar_bboxes by scripts/load_land.py.

    Deliberately not keyed to an ROI: boxes get retuned, and a polygon's job is
    the same whoever asks. Only geometry overlapping some sar_bbox is loaded, so
    the table stays a few MB rather than the source dataset's ~1 GB.
    """

    __tablename__ = "land_polygons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    geom: Mapped[str] = mapped_column(
        Geography(geometry_type="GEOMETRY", srid=4326, spatial_index=True),
        nullable=False,
    )
