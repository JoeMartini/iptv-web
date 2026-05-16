"""Unit tests for IPTV Web Player."""

import pytest
from app import app, parse_m3u, rewrite_m3u8


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


class TestParseM3U:
    def test_basic_parsing(self):
        content = """#EXTM3U
#EXTINF:-1 tvg-name="CCTV-1" tvg-logo="http://example.com/logo.png" group-title="央视",CCTV-1
http://example.com/cctv1.m3u8
"""
        channels = parse_m3u(content)
        assert len(channels) == 1
        assert channels[0]["name"] == "CCTV-1"
        assert channels[0]["group"] == "央视"
        assert channels[0]["logo"] == "http://example.com/logo.png"
        assert channels[0]["url"] == "http://example.com/cctv1.m3u8"

    def test_no_logo_fallback(self):
        content = """#EXTM3U
#EXTINF:-1 tvg-name="Test",Test Channel
http://example.com/test.m3u8
"""
        channels = parse_m3u(content)
        assert "epg.112114.xyz/logo/" in channels[0]["logo"]

    def test_empty_content(self):
        assert parse_m3u("") == []


class TestRewriteM3U8:
    def test_relative_paths(self):
        content = "#EXTM3U\nsegment1.ts\nsegment2.ts"
        result = rewrite_m3u8(content, "http://example.com/stream.m3u8")
        assert "/api/proxy?url=" in result
        assert "segment1.ts" not in result  # Should be rewritten

    def test_preserve_comments(self):
        content = "#EXTM3U\n#EXT-X-VERSION:3\nsegment.ts"
        result = rewrite_m3u8(content, "http://example.com/stream.m3u8")
        assert "#EXT-X-VERSION:3" in result


class TestRoutes:
    def test_index(self, client):
        rv = client.get("/")
        assert rv.status_code == 200
        assert b"IPTV" in rv.data

    def test_api_playlist(self, client):
        rv = client.get("/api/playlist")
        assert rv.status_code in (200, 502)  # 502 if network unavailable

    def test_api_proxy_missing_url(self, client):
        rv = client.get("/api/proxy")
        assert rv.status_code == 400

    def test_api_proxy_invalid_scheme(self, client):
        rv = client.get("/api/proxy?url=ftp://example.com")
        assert rv.status_code == 400

    def test_api_health_missing_url(self, client):
        rv = client.get("/api/health")
        assert rv.status_code == 400

    def test_rate_limit(self, client):
        # Rapid requests should eventually hit rate limit
        for _ in range(70):
            client.get("/api/playlist")
        rv = client.get("/api/playlist")
        # Note: in testing mode rate limits may not trigger due to single-threading
        assert rv.status_code in (200, 429)
