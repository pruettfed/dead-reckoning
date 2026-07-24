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
    vessel_active_minutes: int = Field(default=30, alias="VESSEL_ACTIVE_MINUTES")
    source_stale_after_seconds: float = Field(default=60.0, alias="SOURCE_STALE_AFTER_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # SAR imagery
    cdse_client_id: str | None = Field(default=None, alias="CDSE_CLIENT_ID")
    cdse_client_secret: str | None = Field(default=None, alias="CDSE_CLIENT_SECRET")

    # SAR analysis pipeline
    analysis_api_key: str | None = Field(default=None, alias="ANALYSIS_API_KEY")
    sar_model_path: str = Field(default="models/sar_ship.pt", alias="MODEL_PATH")
    detection_conf_threshold: float = Field(default=0.1, alias="DETECTION_CONF_THRESHOLD")
    fusion_max_distance_m: float = Field(default=500.0, alias="FUSION_MAX_DISTANCE_M")
    fusion_max_time_delta_hours: float = Field(default=2.0, alias="FUSION_MAX_TIME_DELTA_HOURS")
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
