"""Serve the built frontend from the API process, on one origin.

The alternative was a static host for the SPA and the API on its own hostname.
That reintroduces everything single-origin removes: a cross-origin preflight on
every call, a CORS allow-list to keep in sync with the frontend's deployment,
and a second publicly reachable address whose whole surface is the API. Serving
both from here means the browser never makes a cross-origin request at all, so
CORS stops being load-bearing, and the API has no hostname of its own to find.

Mounted only when a build is present. In development Vite serves the SPA on
5173 and proxies /api here, so `dist/` does not exist and the API runs alone —
the same code path, one directory poorer.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# Where the Docker build drops `vite build` output.
DIST = Path(__file__).resolve().parent.parent / "static"

# Vite fingerprints everything under assets/, so those are immutable for a year.
# index.html must not be: it is the file that names the current fingerprints, and
# caching it is how a deploy leaves browsers loading assets that no longer exist.
ASSET_CACHE = "public, max-age=31536000, immutable"
INDEX_CACHE = "no-cache"


class _Assets(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = ASSET_CACHE
        return response


def mount_spa(app: FastAPI, dist: Path = DIST) -> bool:
    """Serve `dist` at the root. Returns whether anything was mounted."""
    index = dist / "index.html"
    if not index.is_file():
        logger.info("no frontend build at %s — serving the API only", dist)
        return False

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", _Assets(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str, request: Request) -> FileResponse:
        """Any unmatched path returns the SPA shell.

        Registered last, so every real route wins first. /api is excluded
        explicitly: without it an unknown endpoint would return index.html with
        a 200, and a client fetching JSON would get HTML and a parse error
        instead of the 404 that actually happened.
        """
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (dist / full_path).resolve()
        # Serve real files at the root (favicon, robots.txt) but never escape
        # dist — full_path is caller-controlled.
        if full_path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": INDEX_CACHE})

    logger.info("serving frontend from %s", dist)
    return True
