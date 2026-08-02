from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Shortest DEVTOOLS_API_KEY / ANALYSIS_API_KEY accepted. These keys guard a
# pixel budget and an unscoped delete; a guessable one is worse than none.
MIN_KEY_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(alias="DATABASE_URL")
    cors_origins: Annotated[list[str], NoDecode] = Field(alias="CORS_ORIGINS")
    # Defaults to production so a forgotten ENV fails closed. Dev sets it
    # explicitly — docker-compose.yml and .env.example both already do.
    env: Literal["development", "staging", "production"] = Field(
        default="production", alias="ENV"
    )

    # AIS
    aisstream_api_key: str | None = Field(default=None, alias="AISSTREAM_API_KEY")
    ais_retention_days: int = Field(default=2, alias="AIS_RETENTION_DAYS")
    vessel_active_minutes: int = Field(default=240, alias="VESSEL_ACTIVE_MINUTES")
    source_stale_after_seconds: float = Field(default=60.0, alias="SOURCE_STALE_AFTER_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # SAR imagery
    cdse_client_id: str | None = Field(default=None, alias="CDSE_CLIENT_ID")
    cdse_client_secret: str | None = Field(default=None, alias="CDSE_CLIENT_SECRET")

    # Developer reset tools. Both must be open for the /api/dev router to exist,
    # and ENV=production forbids them outright — see devtools.py.
    devtools_enabled: bool = Field(default=False, alias="DEVTOOLS_ENABLED")
    devtools_api_key: str | None = Field(default=None, alias="DEVTOOLS_API_KEY")

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

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def devtools_available(self) -> bool:
        """Whether the /api/dev router may be registered at all.

        Deliberately not an error when the key is missing or short: the CLI
        (scripts/dev_reset.py) talks to the database directly and needs no key,
        so a fresh clone must still boot. main.py warns and skips the router.
        """
        return (
            not self.is_production
            and self.devtools_enabled
            and len(self.devtools_api_key or "") >= MIN_KEY_LENGTH
        )

    @model_validator(mode="after")
    def _check_production_invariants(self) -> "Settings":
        """Refuse to boot a production process that is configured unsafely.

        A misconfigured prod deploy must not start and quietly serve a
        destructive surface; failing here is the loudest signal available.
        """
        if not self.is_production:
            return self
        if self.devtools_enabled:
            raise ValueError(
                "DEVTOOLS_ENABLED=true is forbidden when ENV=production: the "
                "developer reset endpoints delete scenes, AIS and the PU ledger"
            )
        if any(origin == "*" for origin in self.cors_origins):
            raise ValueError("CORS_ORIGINS may not contain '*' when ENV=production")
        # An empty key is "not configured", which check_admin_key already
        # answers with a 503. Only a set-but-weak key is a boot error.
        if self.analysis_api_key and len(self.analysis_api_key) < MIN_KEY_LENGTH:
            raise ValueError(
                f"ANALYSIS_API_KEY must be at least {MIN_KEY_LENGTH} characters "
                "when ENV=production"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
