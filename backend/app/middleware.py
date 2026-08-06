"""Security headers and rate limiting for a public, unauthenticated API.

No API key: a public SPA can't hold a secret, so what's available instead is
limiting request rate and telling the browser what the page may do.

Rate limiting is in-process (a single uvicorn worker by construction — see
main.py), so a shared store would add a dependency for nothing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

logger = logging.getLogger(__name__)

# Comfortably above a normal page load's ~dozen calls plus health/schedule polling.
DEFAULT_RATE = 60      # requests
DEFAULT_WINDOW = 60.0  # seconds

# Scene overviews are PNG blobs out of Postgres — costlier than a JSON read.
OVERVIEW_PATH_MARKER = "/overview.png"
OVERVIEW_RATE = 20
OVERVIEW_WINDOW = 60.0

# Idle buckets are dropped on sweep so the table can't grow unbounded.
BUCKET_TTL = 300.0
SWEEP_EVERY = 60.0


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class TokenBuckets:
    """Refilling token buckets, keyed by caller."""

    rate: int
    window: float
    _buckets: dict[str, _Bucket] = field(default_factory=dict)
    _last_sweep: float = 0.0

    def allow(self, key: str, now: float) -> bool:
        self._sweep(now)
        bucket = self._buckets.get(key)
        if bucket is None:
            self._buckets[key] = _Bucket(tokens=self.rate - 1, updated=now)
            return True
        # Continuous refill, not a fixed window — avoids 2x rate across a boundary.
        refill = (now - bucket.updated) * (self.rate / self.window)
        bucket.tokens = min(float(self.rate), bucket.tokens + refill)
        bucket.updated = now
        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True

    def _sweep(self, now: float) -> None:
        if now - self._last_sweep < SWEEP_EVERY:
            return
        self._last_sweep = now
        stale = [k for k, b in self._buckets.items() if now - b.updated > BUCKET_TTL]
        for key in stale:
            del self._buckets[key]


def client_key(request: Request) -> str:
    """Who to charge a request to. --proxy-headers makes request.client.host real."""
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, rate: int = DEFAULT_RATE, window: float = DEFAULT_WINDOW):
        super().__init__(app)
        self._general = TokenBuckets(rate=rate, window=window)
        self._overview = TokenBuckets(rate=OVERVIEW_RATE, window=OVERVIEW_WINDOW)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Static assets are cached by the browser; only /api/ is metered.
        if not path.startswith("/api/"):
            return await call_next(request)
        buckets = self._overview if path.endswith(OVERVIEW_PATH_MARKER) else self._general
        key = client_key(request)
        if not buckets.allow(key, time.monotonic()):
            logger.warning("rate limited %s on %s", key, path)
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(int(buckets.window))},
            )
        return await call_next(request)


# Leaflet needs the CARTO basemap and inline styles (tile positioning); nothing else off-origin.
CSP = "; ".join(
    [
        "default-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https://*.basemaps.cartocdn.com",
        "connect-src 'self'",
    ]
)

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), interest-cohort=()",
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",  # frame-ancestors covers modern browsers; this covers old ones.
}


# Railway's healthcheck prober hits the container over its internal network,
# never through the public custom domain — its Host header can't match
# ALLOWED_HOSTS by construction, so a plain TrustedHostMiddleware fails every
# deploy's healthcheck regardless of whether the app is actually healthy. The
# health endpoint carries nothing sensitive, so it's exempt; every other path
# is still pinned.
HEALTHCHECK_PATH = "/api/health"


class TrustedHostMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, allowed_hosts: list[str]):
        super().__init__(app)
        self._allowed = set(allowed_hosts)

    async def dispatch(self, request: Request, call_next):
        if request.url.path == HEALTHCHECK_PATH:
            return await call_next(request)
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in self._allowed:
            return PlainTextResponse("Invalid host header", status_code=400)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, production: bool):
        super().__init__(app)
        self._headers = dict(BASE_HEADERS)
        if production:
            # HSTS would pin http://localhost to https across every project on the machine.
            self._headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for header, value in self._headers.items():
            response.headers.setdefault(header, value)
        return response
