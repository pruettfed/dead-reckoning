# Deploy runbook: Railway + dark-vessel.pruettfed.com

This is the step-by-step for standing up production and for pushing routine
changes to it afterward. The architecture decisions behind it (why Railway,
why one origin, why the model ships in the image) are in `CLAUDE.md` and
`README.md` — this doc is just the sequence of clicks and commands.

**One-time setup:** Part 1.
**Every time you change code afterward:** Part 2 — read that first if you're
just here to ship a fix.

---

## Part 1 — First-time setup

### 1.1 Merge to `main`

Railway deploys whatever branch you point it at — normally `main`. If you're
reading this because a feature branch has your changes, merge it first. (At
time of writing, `main` already has the deployment-prep work and the
committed `backend/models/sar_ship.pt` checkpoint.)

### 1.2 Create the Railway project

1. [railway.com](https://railway.com) → sign in with GitHub → **New Project**.
2. **Deploy from GitHub repo** → select `pruettfed/dead-reckoning`.
3. Railway will try to build immediately and fail (no database yet, no env
   vars set) — that's expected. Rename this service to **`web`** in its
   settings so the two services in this project are easy to tell apart.
4. Confirm the build source is right: **Settings → Build** should show
   `railway.json` was picked up automatically — Dockerfile builder, path
   `backend/Dockerfile`. If it instead shows Nixpacks, set the builder to
   **Dockerfile** manually and point it at `backend/Dockerfile`.

### 1.3 Add PostGIS

1. In the same project: **New** → **Database** → **Add PostgreSQL**... but
   don't use Railway's stock Postgres template — it has no PostGIS extension,
   and this app needs it (`Geography` columns, `ST_*` queries throughout).
   Instead:
2. **New** → **Empty Service** → name it **`db`**.
3. In `db`'s settings, set the **Source** to a Docker image:
   `postgis/postgis:16-3.4`.
4. **Variables** tab on `db` → add these as literal values (there's no
   upstream service to reference them from — these *are* the source of
   truth, read by the postgis container on its first startup):
   ```
   POSTGRES_USER=dvd
   POSTGRES_PASSWORD=<generate one — see below>
   POSTGRES_DB=dvd
   ```
   Generate the password locally: `openssl rand -hex 24`. Hex output only
   (0-9a-f) is deliberate — this password ends up embedded in a
   `postgresql://user:PASSWORD@host/db` URL below, and hex needs no
   percent-encoding, unlike a password that might contain `@`, `/`, `:`, or `#`.
5. Still on `db` → add one more variable, self-referencing the three above
   (Railway's own Postgres template does this automatically; a bare Docker
   image doesn't, so it needs to be explicit here):
   ```
   DATABASE_URL=postgresql://${{POSTGRES_USER}}:${{POSTGRES_PASSWORD}}@${{RAILWAY_PRIVATE_DOMAIN}}:5432/${{POSTGRES_DB}}
   ```
   `RAILWAY_PRIVATE_DOMAIN` is injected automatically into every service (the
   internal-only `<service>.railway.internal` hostname) — don't set that one.
6. **Settings → Volumes** → add a volume, mount path `/var/lib/postgresql/data`.
   Without this, every redeploy wipes the database.
7. Deploy `db`.

### 1.4 Configure `web`'s environment variables

`web` → **Variables** tab. Add these (values are yours to fill in; the shape
matters):

```
ENV=production
DATABASE_URL=${{db.DATABASE_URL}}
CORS_ORIGINS=https://dark-vessel.pruettfed.com
ALLOWED_HOSTS=dark-vessel.pruettfed.com
AISSTREAM_API_KEY=<your AISStream key>
CDSE_CLIENT_ID=<your CDSE client id>
CDSE_CLIENT_SECRET=<your CDSE client secret>
LOG_LEVEL=INFO
```

Notes:
- `${{db.DATABASE_URL}}` references the `DATABASE_URL` variable you set on
  `db` in step 1.3.5 — click the field, choose "Add Reference", pick `db`.
  It's a plain `postgresql://` URL; the app normalizes the driver prefix
  itself (`config.py`), so you don't need to hand-edit it.
- **Do not set `DEVTOOLS_ENABLED=true`** and don't copy `.env.example`
  verbatim — it defaults that on for local dev, and `ENV=production` will
  refuse to boot with it set. That refusal is intentional; if you see it in
  the deploy logs, it means you copied the wrong file.
- Leave `MODEL_PATH`, `DETECTION_CONF_THRESHOLD`, the scheduler and fusion
  tuning vars unset — their defaults (baked into `config.py`) are what's
  actually running today. Only override one if you're deliberately retuning
  it, and if you do, update `.env.example` to match so the two don't drift.
- `PORT` — don't set this yourself. Railway injects it, and the Dockerfile's
  `CMD` already reads `${PORT:-8000}`.

`web` will redeploy automatically when you save variables. Give it a minute,
then check **Deployments → \[latest\] → View Logs** for:
```
scheduler warming up: no AIS recorded yet ...
```
That's correct on a brand-new database — see Part 1.6.

### 1.5 Point the domain at Railway

Your registrar for `pruettfed.com` is Vercel (Vercel sells/manages domains
separately from Vercel *hosting* — you're not hosting on Vercel here, just
using their DNS panel).

1. Railway: `web` → **Settings → Networking → Custom Domain** → enter
   `dark-vessel.pruettfed.com` → **Add**. Railway shows you a CNAME target
   (something like `xyz.up.railway.app`) and may also show a TXT record for
   verification.
2. Vercel dashboard → your account → **Domains** → `pruettfed.com` → DNS
   records (or **Settings → Domains** depending on where it's registered vs.
   just DNS-managed).
3. Add the records Railway gave you:
   - **CNAME**, name `dark-vessel`, value the Railway target it showed you.
   - **TXT**, if Railway asked for one, same name, the verification value.
4. Back in Railway, wait for the domain to show **Active** (DNS propagation —
   usually minutes, occasionally up to an hour). Railway provisions TLS
   automatically once it verifies.
5. Confirm: `curl -I https://dark-vessel.pruettfed.com/api/health` should
   return `200` with a JSON body, over HTTPS, no cert warning.

Don't touch any other records on `pruettfed.com` — this only adds one CNAME
(and maybe one TXT) under the `dark-vessel` subdomain; the apex domain and
`www` are untouched.

### 1.6 What happens on first boot (expected, not a bug)

- The scheduler holds **every** region — fused and survey — until
  `min(ais_positions.time)` in the database shows `SCHEDULER_WARMUP_HOURS`
  (6h) of depth. You'll see `warming_up` in the logs and in the region rail
  on the site itself for up to 6 hours after the very first boot. This is
  deliberate: without it, a cold deploy buys pixels for the survey regions
  before AIS has had time to establish coverage. A *redeploy* onto an
  already-populated database skips this — it only applies once, on a
  genuinely empty database.
- If `AISSTREAM_API_KEY` is missing or wrong, AIS never populates and the
  scheduler waits out the full 8h cap (`SCHEDULER_WARMUP_MAX_HOURS`) before
  starting anyway — check `/api/health` → `sources.ais.state`.
- If the SAR half never leaves `idle`, check the reason in
  `/api/analysis/schedule` → `scheduler.detail`. The two real causes are
  missing CDSE credentials and a missing/misplaced model checkpoint — the
  latter shouldn't happen since it's committed to `main`, but confirm with:
  ```
  railway run --service web sh -c 'ls -la models/'
  ```

---

## Part 2 — Updating the deployment

This is the part you'll actually use day to day. Short version: **push to
`main`, Railway rebuilds and redeploys automatically.** No separate "deploy"
step, no dashboard click, for either frontend or backend changes — they ship
in the same image (see `CLAUDE.md`: single-origin, one Dockerfile builds
both).

### 2.1 Routine change (the common case)

```bash
git checkout main
git pull

# ... make your change, frontend or backend, doesn't matter which ...

git add -A
git commit -m "describe the change"
git push origin main
```

That push alone triggers Railway. Watch it happen:

- Railway dashboard → `web` → **Deployments** tab shows a new build start
  within a few seconds of the push.
- Or from a terminal: `railway logs --service web` (needs `railway login` and
  `railway link` once per machine — see 2.4).

The build runs the same `backend/Dockerfile` as local: Node stage builds the
SPA (`pnpm build`), Python stage installs deps and copies in `app/`,
`models/`, and the built frontend as `static/`. Typical build time is a few
minutes — most of it is the ML dependency layer, which is cached unless
`requirements-ml.txt` changed.

Railway does a health-checked rollout: the new container has to pass
`GET /api/health` before traffic moves to it (see `railway.json`'s
`healthcheckPath`). If it fails, the old container keeps serving and the
deploy shows as failed — you won't get a silent outage from a bad push.

### 2.2 Frontend-only change

Nothing special — same as 2.1. There's no separate frontend deploy target;
`pnpm build`'s output is baked into the same image and served by the API
process (`app/spa.py`). If you want to sanity-check the build locally before
pushing:

```bash
cd frontend
pnpm build        # same command the Docker build runs
pnpm preview       # serves dist/ locally, not proxied — a real prod-shaped check
```

### 2.3 Backend-only change

Same push, same rebuild. If your change only touches `backend/app/` and you
want a faster local loop before pushing, `docker compose up --build` rebuilds
just the backend against your local Postgres — much faster than a full
Railway build, since it doesn't rebuild the frontend stage.

One thing to check before pushing a backend change: does it add a new
response field or a new settings key?
- **New response field** → add it to the matching model in
  `backend/app/schemas.py`, or FastAPI silently drops it and you'll wonder
  why the frontend isn't seeing it.
- **New setting** → add it to `backend/.env.example` (the documented
  contract) and, if production needs a non-default value, add it as a
  Railway variable too — `.env.example` isn't read in production, it's just
  documentation.

### 2.4 One-time: linking the Railway CLI (optional but useful)

Not required for deploys — pushes to `main` handle that regardless. Useful
for tailing logs or running one-off commands (like `scripts/analyze.py`,
which is the *only* way to force an analysis in production — there's no HTTP
endpoint for it there by design).

```bash
npm install -g @railway/cli   # or: brew install railway
railway login
cd /path/to/dead-reckoning
railway link                  # pick the project, then the `web` service
```

After that:

```bash
railway logs                              # tail web's logs
railway run sh -c 'ls models/'            # run something inside web's environment
railway run python scripts/analyze.py north_taiwan --yes   # force an analysis
```

### 2.5 Changing environment variables without a code push

Some things don't need a git commit — API keys, `SCHEDULER_INTERVAL_SECONDS`,
etc. Edit them directly in the Railway dashboard (`web` → **Variables**).
Saving a variable triggers a redeploy on its own, same health-checked rollout
as a code push, and `main` stays untouched.

### 2.6 Rolling back

Every deploy is kept. `web` → **Deployments** → find the last good one →
**⋮** → **Redeploy**. This re-runs that exact build (or if the image is still
cached, just restarts it) without touching `main` — useful when a push turns
out to be bad and you want to buy time before pushing a fix, rather than
`git revert`-and-push under pressure.

### 2.7 Database schema changes

There's no migration tool (`CLAUDE.md`: "No Alembic in this repo"). New
tables and additive columns (the `apply_schema` pattern in `landmask.py` /
`fusion.py`) pick themselves up on the next boot automatically — nothing to
do. A schema change that isn't purely additive (renaming or dropping a
column) has no automatic path in production; that situation needs a plan of
its own before you push it, not something to discover from a crash loop.

---

## Quick reference

| Task | Command / place |
|---|---|
| Ship a frontend or backend change | `git push origin main` — that's it |
| Watch a deploy | Railway dashboard → `web` → Deployments, or `railway logs` |
| Change a secret/env var | Railway dashboard → `web` → Variables |
| Force an analysis (ops only) | `railway run python scripts/analyze.py <roi> --yes` |
| Roll back | Deployments → pick a prior one → Redeploy |
| Check prod health | `curl https://dark-vessel.pruettfed.com/api/health` |
| Check scheduler state | `curl .../api/analysis/schedule` → `scheduler.state` / `.detail` |
