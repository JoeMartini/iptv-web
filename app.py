#!/usr/bin/env python3
"""
IPTV Web Player - A lightweight web-based IPTV streaming player.

Supports: M3U playlist loading, HLS playback, server-side proxy,
health checking, auto proxy fallback, and mobile-friendly UI.

License: MIT
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import requests
from flask import Flask, Response, jsonify, render_template, request

from config import Config

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("iptv")

# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = Config.PROXY_MAX_CONTENT_LENGTH

# ---------------------------------------------------------------------------
# Rate Limiter (simple in-memory)
# ---------------------------------------------------------------------------
_rate_limit_store: dict[str, list[float]] = {}
_rate_limit_lock = threading.Lock()


def _check_rate_limit(client_ip: str) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    if not Config.RATE_LIMIT_ENABLED:
        return True
    now = time.time()
    with _rate_limit_lock:
        timestamps = _rate_limit_store.get(client_ip, [])
        # Keep only timestamps within the window
        timestamps = [t for t in timestamps if now - t < Config.RATE_LIMIT_WINDOW]
        if len(timestamps) >= Config.RATE_LIMIT_REQUESTS:
            _rate_limit_store[client_ip] = timestamps
            return False
        timestamps.append(now)
        _rate_limit_store[client_ip] = timestamps
        return True


@app.before_request
def _apply_rate_limit() -> Any | None:
    """Apply rate limiting before each request."""
    if request.endpoint in ("static", "index"):
        return None
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    if not _check_rate_limit(client_ip.split(",")[0].strip()):
        logger.warning("Rate limit exceeded for %s", client_ip)
        return jsonify({"error": "Rate limit exceeded. Please slow down."}), 429
    return None


# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------
_health_cache: dict[str, dict] = {}
_playlist_cache: dict[str, Any] = {"channels": None, "fetched_at": 0, "error": None}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Health Checking
# ---------------------------------------------------------------------------
def _check_single_mode(url: str, use_clash: bool = False) -> dict:
    """Check availability of a single channel endpoint.

    Returns a dict with keys: status (green|yellow|red), latency (float),
    error (str|None).
    """
    if url.startswith("rtmp://"):
        return {"status": "red", "latency": None, "error": "RTMP not supported in browser"}

    proxies = Config.CLASH_PROXIES if use_clash else Config.NO_PROXIES
    start = time.time()

    try:
        if ".m3u8" in url or "/tracks-" in url:
            # HLS source: verify m3u8 header + first TS segment
            r = requests.get(
                url,
                timeout=Config.HEALTH_CHECK_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0"},
                stream=True,
                proxies=proxies,
            )
            r.raise_for_status()

            chunk = r.raw.read(2048)
            text = chunk.decode("utf-8", errors="ignore")
            if not text.strip().startswith("#EXTM3U"):
                return {"status": "red", "latency": time.time() - start, "error": "Invalid m3u8"}

            ts_url = None
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    ts_url = line
                    break

            if not ts_url:
                chunk2 = r.raw.read(2048)
                for line in chunk2.decode("utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        ts_url = line
                        break

            if ts_url:
                absolute_ts = urljoin(url, ts_url)
                ts_r = requests.head(
                    absolute_ts,
                    timeout=Config.HEALTH_CHECK_TIMEOUT,
                    headers={"User-Agent": "Mozilla/5.0"},
                    allow_redirects=True,
                    proxies=proxies,
                )
                ts_r.raise_for_status()
                total_latency = time.time() - start
                if ts_r.status_code == 200:
                    return {"status": "green" if total_latency < 2 else "yellow",
                            "latency": round(total_latency, 2), "error": None}
                return {"status": "yellow", "latency": round(total_latency, 2),
                        "error": f"TS {ts_r.status_code}"}
            return {"status": "yellow", "latency": round(time.time() - start, 2),
                    "error": "No media segments"}

        # Non-HLS direct stream: use GET with stream=True to verify
        # HEAD can return 200 for dead endpoints that serve HTML error pages
        r = requests.get(
            url,
            timeout=Config.HEALTH_CHECK_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
            stream=True,
            allow_redirects=True,
            proxies=proxies,
        )
        r.raise_for_status()
        # Read first 1KB to verify it's not an HTML error page
        chunk = r.raw.read(1024)
        content_type = r.headers.get("content-type", "").lower()
        latency = time.time() - start
        # Reject HTML responses (likely error/placeholder pages)
        if "text/html" in content_type and b"<html" in chunk.lower():
            return {"status": "red", "latency": round(latency, 2),
                    "error": "Invalid stream (HTML page)"}
        return {"status": "green" if latency < 2 else "yellow",
                "latency": round(latency, 2), "error": None}

    except Exception as exc:
        return {"status": "red", "latency": round(time.time() - start, 2),
                "error": str(exc)[:80]}


def check_channel(url: str) -> dict:
    """Check both direct and proxy modes for a channel.

    Returns {mode, direct, proxy, checked_at} where mode is one of
    direct|proxy|none|unknown.
    """
    direct = _check_single_mode(url, use_clash=False)
    proxy = _check_single_mode(url, use_clash=True)

    if direct["status"] in ("green", "yellow"):
        mode = "direct"
    elif proxy["status"] in ("green", "yellow"):
        mode = "proxy"
    elif direct["status"] == "red" and proxy["status"] == "red":
        mode = "none"
    else:
        mode = "unknown"

    return {"mode": mode, "direct": direct, "proxy": proxy, "checked_at": time.time()}


def get_channel_health(url: str) -> dict:
    """Return cached health or a default unknown state (non-blocking)."""
    with _cache_lock:
        if url in _health_cache:
            return _health_cache[url]
    return {"mode": "unknown", "direct": {"status": "unknown"},
            "proxy": {"status": "unknown"}, "checked_at": 0}


def check_channels_background(urls: list[str], max_workers: int = 20) -> None:
    """Run health checks in the background and update caches."""
    def _check_one(url: str) -> None:
        result = check_channel(url)
        result["checked_at"] = time.time()
        with _cache_lock:
            _health_cache[url] = result
            channels = _playlist_cache.get("channels")
            if channels:
                for ch in channels:
                    if ch.get("url") == url:
                        ch["health"] = result
                        ch["access_mode"] = result["mode"]
                        break

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        executor.map(_check_one, urls)


# ---------------------------------------------------------------------------
# M3U Parsing
# ---------------------------------------------------------------------------
def parse_m3u(content: str) -> list[dict]:
    """Parse M3U playlist content into a list of channel dicts."""
    channels: list[dict] = []
    current: dict | None = None

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue

        if line.startswith("#EXTINF:"):
            attrs = {k: v for k, v in re.findall(r'([\w-]+)="([^"]*)"', line)}
            name = line.split(",", 1)[-1].strip() if "," in line else "Unknown"
            logo = attrs.get("tvg-logo", "")
            if not logo:
                logo = f"https://epg.112114.xyz/logo/{quote(name, safe='')}.png"
            current = {
                "name": name,
                "group": attrs.get("group-title", "Uncategorized"),
                "logo": logo,
                "url": None,
            }
        elif not line.startswith("#") and current is not None:
            current["url"] = line
            channels.append(current)
            current = None

    return channels


# ---------------------------------------------------------------------------
# Playlist Fetching
# ---------------------------------------------------------------------------
def fetch_playlist(force_refresh: bool = False) -> tuple[list[dict], bool]:
    """Fetch and parse the playlist. Returns (channels, from_cache)."""
    with _cache_lock:
        now = time.time()
        if (
            not force_refresh
            and _playlist_cache["channels"] is not None
            and (now - _playlist_cache["fetched_at"]) < Config.PLAYLIST_CACHE_TTL
        ):
            return _playlist_cache["channels"], True

    try:
        resp = requests.get(Config.PLAYLIST_URL, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Failed to fetch playlist: %s", exc)
        with _cache_lock:
            if _playlist_cache["channels"] is not None:
                return _playlist_cache["channels"], True
        raise

    channels = parse_m3u(resp.text)
    for ch in channels:
        health = get_channel_health(ch["url"])
        ch["health"] = health
        ch["access_mode"] = health.get("mode", "unknown")

    with _cache_lock:
        _playlist_cache["channels"] = channels
        _playlist_cache["fetched_at"] = time.time()
        _playlist_cache["error"] = None

    # Background health checks for unknown channels
    unknown_urls = [ch["url"] for ch in channels if ch.get("access_mode") == "unknown"]
    if unknown_urls:
        t = threading.Thread(
            target=check_channels_background,
            args=(unknown_urls, Config.HEALTH_CHECK_WORKERS),
            daemon=True,
        )
        t.start()

    return channels, False


# ---------------------------------------------------------------------------
# Proxy Helpers
# ---------------------------------------------------------------------------
def rewrite_m3u8(content: str, base_url: str) -> str:
    """Rewrite relative paths inside an m3u8 to absolute proxy URLs."""
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            lines.append(line)
            continue
        absolute = urljoin(base_url, stripped)
        lines.append("/api/proxy?url=" + quote(absolute, safe=""))
    return "\n".join(lines)


def _get_proxies_for_url(url: str) -> dict | None:
    """Decide whether to use Clash proxy based on health cache."""
    health = get_channel_health(url)
    mode = health.get("mode", "unknown")
    if mode == "direct":
        return Config.NO_PROXIES
    return Config.CLASH_PROXIES


def _sanitize_headers(incoming: dict) -> dict:
    """Remove hop-by-hop headers before proxying."""
    excluded = {"host", "connection", "accept-encoding", "transfer-encoding", "content-length"}
    return {k: v for k, v in incoming.items() if k.lower() not in excluded}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/api/playlist")
def api_playlist() -> Any:
    """Return the parsed playlist with health metadata."""
    refresh = request.args.get("refresh", "").lower() == "true"
    try:
        channels, from_cache = fetch_playlist(force_refresh=refresh)
        with _cache_lock:
            cached_at = _playlist_cache["fetched_at"]
        return jsonify({
            "channels": channels,
            "count": len(channels),
            "from_cache": from_cache,
            "cached_at": cached_at,
        })
    except Exception as exc:
        logger.exception("Playlist fetch failed")
        return jsonify({"error": str(exc)}), 502


@app.route("/api/proxy")
def api_proxy() -> Any:
    """Generic proxy endpoint for video streams and other HTTP resources.

    Query parameters:
        url  -- target URL (required, http/https only)
        force_proxy -- "true" to force Clash proxy (optional)
    """
    target = request.args.get("url", "").strip()
    if not target:
        return jsonify({"error": "Missing ?url="}), 400

    parsed = urlparse(target)
    if parsed.scheme not in ("http", "https"):
        return jsonify({"error": "Only http/https allowed"}), 400

    force_proxy = request.args.get("force_proxy", "").lower() == "true"
    proxies = Config.CLASH_PROXIES if force_proxy else _get_proxies_for_url(target)

    try:
        resp = requests.get(
            target,
            headers=_sanitize_headers(dict(request.headers)),
            stream=True,
            timeout=Config.PROXY_TIMEOUT,
            proxies=proxies,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Proxy request failed for %s: %s", target, exc)
        return jsonify({"error": str(exc)[:200]}), 502

    content_type = resp.headers.get("content-type", "").lower()
    is_m3u8 = target.endswith(".m3u8") or "mpegurl" in content_type or "m3u8" in content_type

    if is_m3u8:
        m3u8_content = resp.content.decode("utf-8", errors="ignore")
        rewritten = rewrite_m3u8(m3u8_content, target)
        return Response(
            rewritten,
            status=200,
            headers={
                "Content-Type": "application/vnd.apple.mpegurl",
                "Access-Control-Allow-Origin": "*",
            },
        )

    # Stream passthrough (exclude hop-by-hop headers)
    excluded = {"transfer-encoding", "content-encoding", "connection", "content-length"}
    response_headers = {
        k: v for k, v in resp.headers.items() if k.lower() not in excluded
    }

    return Response(
        resp.iter_content(chunk_size=65536),
        status=resp.status_code,
        headers=response_headers,
        direct_passthrough=True,
    )


@app.route("/api/health")
def api_health() -> Any:
    """Batch health check for provided URLs."""
    urls = request.args.getlist("url")
    if not urls:
        return jsonify({"error": "Missing ?url="}), 400
    return jsonify({"results": [{"url": u, **get_channel_health(u)} for u in urls]})


@app.route("/")
def index() -> Any:
    """Serve the main application page."""
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    issues = Config.validate()
    if issues:
        for issue in issues:
            logger.error("Config issue: %s", issue)
        raise SystemExit(1)

    logger.info("🚀 IPTV Web Player starting on %s:%s", Config.HOST, Config.PORT)
    app.run(host=Config.HOST, port=Config.PORT, threaded=True, debug=Config.DEBUG)
