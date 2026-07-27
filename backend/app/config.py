from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(alias="DATABASE_URL")
    cors_origins: Annotated[list[str], NoDecode] = Field(alias="CORS_ORIGINS")
    env: str = Field(default="development", alias="ENV")

    # AIS
    aisstream_api_key: str | None = Field(default=None, alias="AISSTREAM_API_KEY")
    ais_retention_days: int = Field(default=2, alias="AIS_RETENTION_DAYS")
    vessel_active_minutes: int = Field(default=240, alias="VESSEL_ACTIVE_MINUTES")
    source_stale_after_seconds: float = Field(default=60.0, alias="SOURCE_STALE_AFTER_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # SAR imagery
    cdse_client_id: str | None = Field(default=None, alias="CDSE_CLIENT_ID")
    cdse_client_secret: str | None = Field(default=None, alias="CDSE_CLIENT_SECRET")

    # SAR analysis pipeline
    analysis_api_key: str | None = Field(default=None, alias="ANALYSIS_API_KEY")
    sar_model_path: str = Field(default="models/sar_ship.pt", alias="MODEL_PATH")
    detection_conf_threshold: float = Field(default=0.1, alias="DETECTION_CONF_THRESHOLD")
    # AIS buffer depth required before a scene may be fused; not a match parameter.
    fusion_max_time_delta_hours: float = Field(default=2.0, alias="FUSION_MAX_TIME_DELTA_HOURS")

    # Automatic analysis scheduler
    scheduler_enabled: bool = Field(default=True, alias="SCHEDULER_ENABLED")
    scheduler_interval_seconds: float = Field(default=900.0, alias="SCHEDULER_INTERVAL_SECONDS")
    pu_monthly_ceiling: float = Field(default=25_000.0, alias="PU_MONTHLY_CEILING")

    # Fusion by dead reckoning — measured defaults
    ais_fix_max_age_s: float = Field(default=1800.0, alias="AIS_FIX_MAX_AGE_S")
    # Agreement required of a stationary vessel; speed terms are added per candidate.
    match_radius_m: float = Field(default=200.0, alias="MATCH_RADIUS_M")
    # SAR azimuth displacement (slant range / platform velocity) for Sentinel-1 IW.
    sar_azimuth_shift_s: float = Field(default=90.0, alias="SAR_AZIMUTH_SHIFT_S")
    # Fraction of dead-reckoned travel treated as uncertain; widens the envelope only.
    dr_course_err_frac: float = Field(default=0.25, alias="DR_COURSE_ERR_FRAC")
    # Above this false-match rate a scene cannot discriminate, so darks are withheld.
    max_chance_match_rate: float = Field(default=0.10, alias="MAX_CHANCE_MATCH_RATE")
    # Seaward metres added to the coastline when masking detections. 0 masks
    # only what is strictly on land; widening it starts eating berthed and
    # anchored vessels. Retuning is free — see landmask.py.
    land_mask_buffer_m: float = Field(default=0.0, alias="LAND_MASK_BUFFER_M")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
