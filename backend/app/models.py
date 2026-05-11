from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import BigInteger, DateTime, Float, Index, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

__all__ = ["Base", "AISPosition", "ShipMetadata"]


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

    Ship type is stored as the raw AIS numeric code (0–99) so the frontend can
    translate it however it likes. Common values: 70 = Cargo, 80 = Tanker.
    """

    __tablename__ = "ship_metadata"

    mmsi: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    ship_name: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ship_type: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    callsign: Mapped[str | None] = mapped_column(String(7), nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
