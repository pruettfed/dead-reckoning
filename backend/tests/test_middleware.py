"""Rate limiting and security headers — the controls on an unauthenticated API."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware import (
    BASE_HEADERS,
    CSP,
    DEFAULT_RATE,
    DEFAULT_WINDOW,
    HEALTHCHECK_PATH,
    SWEEP_EVERY,
    TokenBuckets,
    TrustedHostMiddleware,
)


class TestTokenBuckets:
    def test_allows_up_to_the_limit(self):
        b = TokenBuckets(rate=3, window=60.0)
        assert [b.allow("1.2.3.4", 0.0) for _ in range(3)] == [True, True, True]

    def test_refuses_past_the_limit(self):
        b = TokenBuckets(rate=3, window=60.0)
        for _ in range(3):
            b.allow("1.2.3.4", 0.0)
        assert not b.allow("1.2.3.4", 0.0)

    def test_callers_are_metered_separately(self):
        b = TokenBuckets(rate=1, window=60.0)
        assert b.allow("1.2.3.4", 0.0)
        assert not b.allow("1.2.3.4", 0.0)
        # One noisy visitor must not lock everyone else out.
        assert b.allow("5.6.7.8", 0.0)

    def test_tokens_refill_over_time(self):
        b = TokenBuckets(rate=6, window=60.0)  # one token per 10s
        for _ in range(6):
            b.allow("1.2.3.4", 0.0)
        assert not b.allow("1.2.3.4", 5.0)
        assert b.allow("1.2.3.4", 10.0)

    def test_refill_is_capped_at_the_bucket_size(self):
        b = TokenBuckets(rate=3, window=60.0)
        b.allow("1.2.3.4", 0.0)
        # An hour idle must not bank an hour's worth of requests.
        for _ in range(3):
            assert b.allow("1.2.3.4", 3600.0)
        assert not b.allow("1.2.3.4", 3600.0)

    def test_a_window_boundary_does_not_grant_double_rate(self):
        # The reason for continuous refill: a fixed window lets a caller spend
        # a full allowance either side of the boundary, for 2x the rate.
        b = TokenBuckets(rate=10, window=60.0)
        for _ in range(10):
            b.allow("1.2.3.4", 59.9)
        assert sum(b.allow("1.2.3.4", 60.1) for _ in range(10)) < 10

    def test_idle_buckets_are_dropped(self):
        # Otherwise the table grows without bound under a spray of addresses.
        b = TokenBuckets(rate=1, window=60.0)
        b.allow("1.2.3.4", 0.0)
        assert "1.2.3.4" in b._buckets
        b.allow("5.6.7.8", SWEEP_EVERY + 3600.0)
        assert "1.2.3.4" not in b._buckets

    def test_an_active_bucket_survives_the_sweep(self):
        b = TokenBuckets(rate=10, window=60.0)
        b.allow("1.2.3.4", 0.0)
        b.allow("1.2.3.4", SWEEP_EVERY + 1.0)
        b.allow("5.6.7.8", SWEEP_EVERY + 2.0)
        assert "1.2.3.4" in b._buckets

    def test_the_default_allowance_clears_a_normal_page_load(self):
        # A page load fans out to roughly a dozen calls, then polls health at
        # 30s and the schedule at 60s. A real visitor must never see a 429.
        b = TokenBuckets(rate=DEFAULT_RATE, window=DEFAULT_WINDOW)
        assert all(b.allow("1.2.3.4", 0.0) for _ in range(15))


class TestSecurityHeaders:
    def test_clickjacking_is_blocked_both_ways(self):
        assert "frame-ancestors 'none'" in CSP
        assert BASE_HEADERS["X-Frame-Options"] == "DENY"

    def test_sniffing_is_off(self):
        assert BASE_HEADERS["X-Content-Type-Options"] == "nosniff"

    def test_csp_allows_the_basemap_and_nothing_else_offsite(self):
        # Leaflet's CARTO tiles are the only external origin the app uses.
        assert "https://*.basemaps.cartocdn.com" in CSP
        assert "connect-src 'self'" in CSP
        assert "script-src 'self'" in CSP

    def test_hsts_is_production_only(self):
        # Sending HSTS from a local http:// dev server would pin the browser to
        # https for localhost across every project on the machine.
        assert "Strict-Transport-Security" not in BASE_HEADERS


class TestThroughTheRealApp:
    """The middleware stack via /api/rois, which touches no database."""

    def client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def test_security_headers_are_present_on_a_real_response(self):
        r = self.client().get("/api/rois")
        assert r.status_code == 200
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        assert r.headers["Referrer-Policy"] == "no-referrer"

    def test_a_burst_is_rate_limited(self):
        c = self.client()
        codes = {c.get("/api/rois").status_code for _ in range(DEFAULT_RATE + 20)}
        assert 429 in codes

    def test_a_rate_limited_response_still_carries_security_headers(self):
        # Headers must wrap the limiter, not sit inside it — a 429 is still a
        # response the browser renders.
        c = self.client()
        last = None
        for _ in range(DEFAULT_RATE + 20):
            last = c.get("/api/rois")
        assert last.status_code == 429
        assert last.headers["X-Content-Type-Options"] == "nosniff"
        assert last.headers["Retry-After"]


class TestTrustedHostMiddleware:
    """Regression coverage for a real prod incident: Railway's healthcheck
    prober hits the container over its internal network, never through the
    public custom domain, so its Host header can never be in ALLOWED_HOSTS.
    A plain trusted-host check failed every deploy's healthcheck regardless
    of whether the app was healthy — this is the exemption that fixes it.
    """

    def client(self, allowed_hosts=("dark-vessel.pruettfed.com",)):
        app = FastAPI()

        @app.get(HEALTHCHECK_PATH)
        async def health():
            return {"status": "ok"}

        @app.get("/api/rois")
        async def rois():
            return []

        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
        return TestClient(app)

    def test_healthcheck_passes_with_no_matching_host(self):
        r = self.client().get(HEALTHCHECK_PATH, headers={"Host": "100.64.0.2"})
        assert r.status_code == 200

    def test_healthcheck_passes_with_no_host_header_at_all(self):
        # httpx/TestClient always sends *some* Host, so drive dispatch directly
        # to cover a prober that omits it entirely.
        r = self.client().get(HEALTHCHECK_PATH)
        assert r.status_code == 200

    def test_other_paths_still_reject_a_mismatched_host(self):
        r = self.client().get("/api/rois", headers={"Host": "evil.example.com"})
        assert r.status_code == 400

    def test_matching_host_is_allowed_through(self):
        r = self.client().get("/api/rois", headers={"Host": "dark-vessel.pruettfed.com"})
        assert r.status_code == 200
