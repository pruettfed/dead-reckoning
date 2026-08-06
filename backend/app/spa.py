"""Serve the built frontend from the API process, on one origin.

Avoids a second public hostname and a CORS allow-list to keep in sync.
Mounted only when a build is present — in dev, Vite serves the SPA instead.
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

# Assets are content-hashed and immutable; index.html names the current hashes, so it isn't.
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
        """Any unmatched path returns the SPA shell. /api excluded so a miss 404s, not 200s HTML."""
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (dist / full_path).resolve()
        # full_path is caller-controlled — must not escape dist.
        if full_path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": INDEX_CACHE})

    logger.info("serving frontend from %s", dist)
    return True
