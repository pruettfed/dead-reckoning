# Scaffold smoke test — 2026-05-08

First end-to-end run of the docker-compose scaffold. Captured here so we can
diff against future runs (or a future me) and not relitigate setup choices.

## What was verified

| Check | Command | Result |
| --- | --- | --- |
| Compose syntax | `docker compose config` | OK |
| Stack boots (default profile) | `docker compose up --build` | `db` healthy, `backend` on `:8000` |
| API health | `curl http://localhost:8000/api/health` | `{"status":"ok"}` (200) |
| API vessels stub | `curl http://localhost:8000/api/vessels` | `[]` (200) |
| PostGIS extension loaded | `docker compose exec db psql -U dvd -d dvd -tAc "SELECT postgis_version();"` | `3.4 USE_GEOS=1 USE_PROJ=1 USE_STATS=1` |
| Frontend profile | `docker compose --profile frontend up --build` | Vite on `:5173`, HTTP 200 |
| Vite dev proxy | `curl http://localhost:5173/api/health` | `{"status":"ok"}` (200) — proves `/api` → `http://backend:8000` works inside the compose network |
| HTML mount | `curl http://localhost:5173/` | `index.html` served, `<div id="root">` + `/src/main.tsx` script tag present |

## Resolved boot issue

- **Symptom:** backend crashed on startup with
  `pydantic_settings.exceptions.SettingsError: error parsing value for field "cors_origins" from source "EnvSettingsSource"` →
  `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`.
- **Cause:** pydantic-settings 2.x JSON-decodes any field whose annotation is a
  complex type (here `list[str]`) *before* `field_validator(mode="before")` is
  invoked. So `CORS_ORIGINS=http://localhost:5173` was being passed through
  `json.loads(...)` and blowing up.
- **Fix:** annotate the field with `NoDecode` so pydantic-settings hands the raw
  string to the validator. See [backend/app/config.py](../backend/app/config.py):
  ```python
  cors_origins: Annotated[list[str], NoDecode] = Field(alias="CORS_ORIGINS")
  ```
  Apply the same pattern to any future comma-separated env var (e.g. allowed
  hosts, feature flags).

## Environment notes

- **Apple Silicon, Rosetta emulation.** `postgis/postgis:16-3.4` has no native
  arm64 image; Docker pulls the `linux/amd64` build and emulates. Works, but
  startup and queries are noticeably slower than native. If this becomes
  annoying, swap to `postgis/postgis:17-3.5-alpine` (multi-arch) or
  `imresamu/postgis-arm64:16-3.4`. Not urgent — schema decisions don't change.
- **Versions installed (snapshot, unpinned in requirements.txt):** fastapi
  0.136.1, pydantic 2.13.4, pydantic-settings 2.14.1, sqlalchemy 2.0.49,
  asyncpg 0.31.0, geoalchemy2 0.19.0, uvicorn 0.46.0. Run
  `pip freeze > requirements.lock` once the API stabilizes.
- **Docker:** daemon 29.4.2, CLI 27.5.1, compose v2.32.4.

## Day-to-day commands

- `docker compose up` — backend + PostGIS
- `docker compose --profile frontend up` — all three services
- `cd frontend && pnpm install && pnpm dev` — native UI dev (faster HMR than
  the container; proxy falls back to `http://localhost:8000`)
- `docker compose down` — stop. `docker compose down -v` also wipes the
  `postgres_data` volume.

## Next checkpoint

Re-run this checklist after the AIS Position model + Alembic land. Add new
rows for: `alembic upgrade head` succeeds; `/api/vessels` returns rows from a
seed; PostGIS spatial query (`ST_DWithin`) works against the new table.
