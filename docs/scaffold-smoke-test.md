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

# Weeks 1–3 — AIS ingestion — 2026-05-10

Live AISStream ingest into PostGIS, time-aware read endpoints, hourly retention
prune, and pytest suite. Alembic still deferred — schema lands via
`Base.metadata.create_all` in lifespan.

## What was verified

| Check | Command | Result |
| --- | --- | --- |
| `ais_positions` schema | `docker compose exec db psql -U dvd -d dvd -c "\d ais_positions"` | id (bigint pk), mmsi (bigint), time (timestamptz), location (`geography(Point,4326)`), sog/cog (float), true_heading/nav_status (smallint); indexes: pk, GIST on location, btree(mmsi, time) |
| Geography round-trip | `INSERT ... ST_GeogFromText('SRID=4326;POINT(...)')` then `ST_Y/ST_X` | values round-trip exactly; `ST_Within` against ROI envelope returns the row |
| AISStream connect & subscribe | tail `docker compose logs backend` | `connected; subscribed (south_china_sea)`; reconnect/backoff path exists but not exercised |
| Position decoding & batching | `flushed N rows` log lines (1–5 rows per ~1s batch) | sustained ingest, ~50–90 rows/min in SCS off-peak; 54 distinct MMSI in first ~50s |
| Retention prune | `pruned 0 rows older than 7 days` log on startup | task runs at startup, then hourly |
| Clean shutdown | `docker compose stop backend`, scan logs | final `flushed 1 rows` then `Shutting down`; no `Task was destroyed but it is pending!` warnings |
| `GET /api/rois` | `curl :8000/api/rois` | 4 ROIs returned with `name`, `label`, `bbox` |
| `GET /api/vessels` (default) | `curl :8000/api/vessels` | `count=67` latest-per-MMSI inside SCS, last 6h |
| `GET /api/vessels?at=...` | `curl ":8000/api/vessels?at=2026-05-11T03:18:00Z"` and `...03:21:00Z` | counts differ across times (24 vs 66) — proves time-offset query works |
| `GET /api/vessels/{mmsi}/track` | `curl ":8000/api/vessels/413496590/track?hours=1"` | time-ordered ascending positions; `hours=99999` clamped to `7*24` |
| Vite proxy → live data | `curl :5173/api/vessels` | 91 vessels via the dev server proxy |
| pytest suite | `cd backend && .venv/bin/pytest` | 17 passed (ais parser + ROI registry) |

## Notes

- **Volume of data:** SCS off-peak yields ~50–90 PositionReport messages/minute. With 7-day retention this caps `ais_positions` at ~1M rows in the busiest case — small for PostGIS, no partitioning needed yet.
- **AISStream timestamp quirk:** messages carry Go-formatted nanosecond UTC strings (`"2024-08-30 13:24:32.987532323 +0000 UTC"`). `app.ais._parse_aisstream_time` truncates to microseconds for `datetime.fromisoformat`. Covered by `tests/test_ais.py::test_parses_full_position_report`.
- **MMSI fallback:** AISStream sometimes omits `MetaData.MMSI` and only carries the id in `Message.PositionReport.UserID`. Parser tries both, in that order.
- **Bbox corner order:** AISStream wants `[[SW lat, SW lon], [NE lat, NE lon]]` — lat-first, opposite of GeoJSON. Easy to break; covered by `tests/test_ais.py::test_subscribe_message_uses_aisstream_corner_order`.
- **Retention deletes via raw SQL** (`DELETE ... WHERE time < now() - make_interval(days => :d)`) — Postgres-specific, fine on PostGIS.
- **`/api/vessels?at=` accepts ISO-8601;** naive datetimes are treated as UTC. The 6-hour trailing window is hardcoded for now; revisit if the frontend timeline needs configurable depth.

## Known limitations (intentional)

- Live ROI switching not implemented — restart required to change `ACTIVE_ROI`.
- No `ShipStaticData` ingestion — vessel names are not yet stored.
- No Alembic — first column change will require introducing it.
