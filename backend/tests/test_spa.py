"""Serving the SPA and the API from one origin — no CORS, no second public hostname."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.spa import ASSET_CACHE, INDEX_CACHE, mount_spa


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><body>SPA SHELL</body>")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc123.css").write_text("body{}")
    (tmp_path / "favicon.ico").write_text("icon")
    return tmp_path


@pytest.fixture
def client(dist):
    app = FastAPI()

    @app.get("/api/rois")
    async def rois():
        return []

    assert mount_spa(app, dist)
    return TestClient(app)


def test_nothing_is_mounted_without_a_build(tmp_path):
    # Development: Vite serves the SPA on 5173 and proxies /api here, so there
    # is no dist/ and the API must still come up.
    assert not mount_spa(FastAPI(), tmp_path)


def test_root_serves_the_shell(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "SPA SHELL" in r.text


def test_a_client_side_route_serves_the_shell(client):
    # The router runs in the browser, so a deep link has no server route.
    r = client.get("/region/north_taiwan")
    assert r.status_code == 200
    assert "SPA SHELL" in r.text


def test_api_routes_still_win(client):
    assert client.get("/api/rois").status_code == 200


def test_an_unknown_api_path_404s_instead_of_returning_html(client):
    # Without the /api exclusion the catch-all answers 200 with index.html, and
    # a client expecting JSON reports a parse error instead of the 404 that
    # actually happened.
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert "SPA SHELL" not in r.text


def test_real_files_at_the_root_are_served(client):
    assert client.get("/favicon.ico").text == "icon"


def test_hashed_assets_are_cached_hard(client):
    assert client.get("/assets/index-abc123.css").headers["Cache-Control"] == ASSET_CACHE


def test_the_shell_is_not_cached(client):
    # index.html names the current asset fingerprints. Caching it is how a
    # deploy leaves browsers asking for files that no longer exist.
    assert client.get("/").headers["Cache-Control"] == INDEX_CACHE


def test_traversal_cannot_escape_the_build_directory(client, dist, tmp_path):
    secret = tmp_path.parent / "outside.txt"
    secret.write_text("SHOULD NOT BE SERVED")
    r = client.get("/../outside.txt")
    assert "SHOULD NOT BE SERVED" not in r.text
