"""Security headers and rate limiting for a public, unauthenticated API.

Every read endpoint here is open by design: the client is a static SPA served
from the same origin, and a browser cannot hold a secret, so an API key baked
into the bundle would be decoration rather than a control. What is available
instead is limiting how fast anyone can pull, and telling the browser what the
page is allowed to do.

Rate limiting is a token bucket held in this process. That is the correct scope
rather than a compromise: the scheduler, the AIS ingest and the retention sweep
are all lifespan tasks, so the app runs as a single uvicorn worker by
construction — a second worker would duplicate the AISStream subscription and
race the PU ledger. With one worker, in-process state is the whole state, and a
shared store would add a dependency for nothing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# Ordinary API reads. The UI polls health every 30 s and the schedule every
# 60 s, and a page load fans out to roughly a dozen calls, so a burst well
# above that is still comfortably interactive for a real visitor.
DEFAULT_RATE = 60      # requests
DEFAULT_WINDOW = 60.0  # seconds

# Scene overviews are PNG blobs streamed straight out of Postgres, so they cost
# far more per request than a JSON read. Leaflet fetches one per visible scene,
# hence a real allowance rather than a token one.
OVERVIEW_PATH_MARKER = "/overview.png"
OVERVIEW_RATE = 20
OVERVIEW_WINDOW = 60.0

# Stop the bucket table from being a memory leak under a spray of source
# addresses. Buckets idle longer than this are dropped on the next sweep.
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
        # Continuous refill rather than a fixed window: a fixed window lets a
        # caller spend a full allowance on either side of the boundary and get
        # double the rate across it.
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
    """Who to charge a request to.

    uvicorn runs with --proxy-headers behind the platform's load balancer, so
    request.client.host is already the real caller. Reading X-Forwarded-For
    here as well would let anyone reset their own bucket by sending the header.
    """
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, rate: int = DEFAULT_RATE, window: float = DEFAULT_WINDOW):
        super().__init__(app)
        self._general = TokenBuckets(rate=rate, window=window)
        self._overview = TokenBuckets(rate=OVERVIEW_RATE, window=OVERVIEW_WINDOW)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Static assets are content-hashed and cached by the browser; limiting
        # them would throttle the page rather than the API.
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


# Leaflet needs the CARTO basemap and its own inline styles; nothing else
# reaches off-origin. 'unsafe-inline' for styles is Leaflet positioning tiles
# via style attributes — there is no build-time nonce that survives that.
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
    # frame-ancestors already covers this for modern browsers; kept for old ones.
    "X-Frame-Options": "DENY",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, production: bool):
        super().__init__(app)
        self._headers = dict(BASE_HEADERS)
        if production:
            # Only in production: sending HSTS from a local http:// dev server
            # would pin the browser to https for localhost across every project
            # on the machine.
            self._headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for header, value in self._headers.items():
            response.headers.setdefault(header, value)
        return response
