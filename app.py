#!/usr/bin/env python3
"""
IPTV Web Player - A lightweight web-based IPTV streaming player.

Supports: M3U playlist loading, HLS playback, server-side proxy,
health checking, auto proxy fallback, and mobile-friendly UI.

License: MIT
"""

from __future__ import annotations

import hashlib
import json
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
# Persistent Cache Helpers
# ---------------------------------------------------------------------------
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
_HEALTH_CACHE_FILE = os.path.join(_CACHE_DIR, ".health_cache.json")
_PLAYLIST_HASH_FILE = os.path.join(_CACHE_DIR, ".playlist_hash.json")


def _load_json_cache(path: str, default: Any) -> Any:
    """Load JSON cache from disk if it exists."""
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load cache from %s: %s", path, exc)
    return default


def _save_json_cache(path: str, data: Any) -> None:
    """Save JSON cache to disk atomically."""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("Failed to save cache to %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------
_health_cache: dict[str, dict] = _load_json_cache(_HEALTH_CACHE_FILE, {})
_playlist_hash: str = _load_json_cache(_PLAYLIST_HASH_FILE, "")
_playlist_cache: dict[str, Any] = {"channels": None, "fetched_at": 0, "error": None}
_cache_lock = threading.Lock()


def _save_health_cache() -> None:
    """Persist health cache to disk."""
    with _cache_lock:
        _save_json_cache(_HEALTH_CACHE_FILE, _health_cache)


def _save_playlist_hash(h: str) -> None:
    """Persist playlist hash to disk."""
    global _playlist_hash
    _playlist_hash = h
    _save_json_cache(_PLAYLIST_HASH_FILE, h)


# ---------------------------------------------------------------------------
# Health Checking
# ---------------------------------------------------------------------------
import gzip


def _decompress_if_gzip(data: bytes) -> bytes:
    """Decompress gzip data if the magic header is present."""
    if data[:2] == b'\x1f\x8b':
        try:
            return gzip.decompress(data)
        except Exception:
            pass
    return data


def _is_m3u8_response(resp, chunk: bytes) -> bool:
    """Detect if a response is an M3U8 playlist, handling gzip compression."""
    content_type = resp.headers.get("content-type", "").lower()
    if "mpegurl" in content_type or "m3u8" in content_type:
        return True
    if resp.url.endswith(".m3u8") or "/tracks-" in resp.url:
        return True
    # Check body (handle gzip)
    body = _decompress_if_gzip(chunk)
    try:
        text = body.decode("utf-8", errors="ignore")
        return text.strip().startswith("#EXTM3U")
    except Exception:
        return False


def _detect_stream_format(chunk: bytes) -> str | None:
    """Detect stream format from first bytes. Returns None if unknown."""
    if len(chunk) < 4:
        return None
    if chunk[:3] == b"FLV":
        return "FLV"
    if chunk[4:8] == b"ftyp":
        return "MP4"
    if chunk[:4] == b"\x1aE\xdf\xa3":
        return "MKV/WebM"
    # MPEG-TS sync byte
    if chunk[0:1] == b"\x47":
        return "MPEG-TS"
    return None


def _check_single_mode(url: str, use_clash: bool = False) -> dict:
    """Check availability of a single channel endpoint.

    Returns a dict with keys: status (green|yellow|red), latency (float),
    error (str|None).
    """
    if url.startswith("rtmp://"):
        return {"status": "red", "latency": None, "error": "RTMP not supported in browser", "format": "RTMP"}

    proxies = Config.CLASH_PROXIES if use_clash else Config.NO_PROXIES
    start = time.time()

    try:
        # Detect HLS by URL pattern or actual response content (handles gzip)
        is_hls = ".m3u8" in url or "/tracks-" in url
        if not is_hls:
            # Quick probe to detect HLS sources without .m3u8 suffix
            probe = requests.get(
                url,
                timeout=Config.HEALTH_CHECK_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0"},
                stream=True,
                proxies=proxies,
            )
            probe.raise_for_status()
            chunk = probe.raw.read(4096)
            is_hls = _is_m3u8_response(probe, chunk)
            probe.close()

        if is_hls:
            # HLS source: verify m3u8 header + first TS segment
            r = requests.get(
                url,
                timeout=Config.HEALTH_CHECK_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0"},
                stream=True,
                proxies=proxies,
            )
            r.raise_for_status()

            chunk = r.raw.read(4096)
            chunk = _decompress_if_gzip(chunk)
            text = chunk.decode("utf-8", errors="ignore")
            if not text.strip().startswith("#EXTM3U"):
                return {"status": "red", "latency": time.time() - start, "error": "Invalid m3u8", "format": "HLS"}

            # Collect all non-comment lines (handle master playlists with chunklists)
            media_urls = []
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    media_urls.append(line)

            if not media_urls:
                chunk2 = r.raw.read(4096)
                chunk2 = _decompress_if_gzip(chunk2)
                for line in chunk2.decode("utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        media_urls.append(line)

            if media_urls:
                # For master playlists, follow the first stream URL to find actual TS
                first_url = media_urls[0]
                resolved = urljoin(url, first_url)

                # If first URL looks like another m3u8 (master playlist), recurse into it
                if ".m3u8" in resolved.lower() or not resolved.lower().endswith(('.ts', '.m4s', '.mp4')):
                    try:
                        sub_r = requests.get(
                            resolved,
                            timeout=Config.HEALTH_CHECK_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0"},
                            stream=True,
                            proxies=proxies,
                        )
                        sub_r.raise_for_status()
                        sub_chunk = sub_r.raw.read(4096)
                        sub_chunk = _decompress_if_gzip(sub_chunk)
                        sub_text = sub_chunk.decode("utf-8", errors="ignore")
                        # Only recurse if sub-response is actually an M3U8 playlist
                        if sub_text.strip().startswith("#EXTM3U"):
                            for line in sub_text.splitlines():
                                line = line.strip()
                                if line and not line.startswith("#"):
                                    resolved = urljoin(resolved, line)
                                    break
                        # else: resolved already points to a TS segment, keep it
                        sub_r.close()
                    except Exception:
                        pass

                # Check actual media segment (GET not HEAD, some servers reject HEAD)
                ts_r = requests.get(
                    resolved,
                    timeout=Config.HEALTH_CHECK_TIMEOUT,
                    headers={"User-Agent": "Mozilla/5.0"},
                    stream=True,
                    proxies=proxies,
                )
                ts_r.raise_for_status()
                ts_chunk = ts_r.raw.read(1024)
                ts_r.close()
                total_latency = time.time() - start
                if ts_r.status_code == 200 and len(ts_chunk) > 0:
                    return {"status": "green" if total_latency < 2 else "yellow",
                            "latency": round(total_latency, 2), "error": None, "format": "HLS"}
                return {"status": "yellow", "latency": round(total_latency, 2),
                        "error": f"TS {ts_r.status_code}", "format": "HLS"}
            return {"status": "yellow", "latency": round(time.time() - start, 2),
                    "error": "No media segments", "format": "HLS"}

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
        chunk = _decompress_if_gzip(chunk)
        content_type = r.headers.get("content-type", "").lower()
        latency = time.time() - start
        # Reject HTML responses (likely error/placeholder pages)
        if "text/html" in content_type and b"<html" in chunk.lower():
            return {"status": "red", "latency": round(latency, 2),
                    "error": "Invalid stream (HTML page)", "format": "HTML"}
        # Detect browser-incompatible formats (FLV, etc.)
        fmt = _detect_stream_format(chunk)
        if fmt and fmt not in ("MPEG-TS", "MP4"):
            return {"status": "red", "latency": round(latency, 2),
                    "error": f"Unsupported format: {fmt}", "format": fmt}
        fmt_ok = _detect_stream_format(chunk)
        return {"status": "green" if latency < 2 else "yellow",
                "latency": round(latency, 2), "error": None,
                "format": fmt_ok or "unknown"}

    except Exception as exc:
        return {"status": "red", "latency": round(time.time() - start, 2),
                "error": str(exc)[:80], "format": "unknown"}


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

    # Determine format from whichever mode succeeded, or from error details
    fmt = "unknown"
    if direct.get("format") and direct["format"] != "unknown":
        fmt = direct["format"]
    elif proxy.get("format") and proxy["format"] != "unknown":
        fmt = proxy["format"]
    return {"mode": mode, "direct": direct, "proxy": proxy, "checked_at": time.time(), "format": fmt}


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
        # Persist after each check
        _save_health_cache()

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
    """Fetch and parse the M3U playlist with caching.

    Returns (channels, from_cache).
    """
    global _playlist_cache
    now = time.time()
    cache_ttl = 300  # 5 minutes

    with _cache_lock:
        cached = _playlist_cache.get("channels")
        cached_at = _playlist_cache.get("fetched_at", 0)
        if cached and not force_refresh and (now - cached_at) < cache_ttl:
            # Enrich cached channels with latest health data
            for ch in cached:
                h = _health_cache.get(ch.get("url"))
                if h:
                    ch["health"] = h
                    ch["access_mode"] = h.get("mode", "unknown")
            return cached, True

    all_channels = []
    all_contents = []
    errors = []

    # Try local playlist file first if no URLs configured
    local_playlist = os.path.join(os.path.dirname(__file__), "playlist.m3u")
    if not Config.PLAYLIST_URLS and os.path.exists(local_playlist):
        try:
            logger.info("Loading local playlist from %s", local_playlist)
            with open(local_playlist, "r", encoding="utf-8") as f:
                content = f.read()
            all_contents.append(content)
            channels = parse_m3u(content)
            logger.info("Parsed %d channels from local file", len(channels))
            all_channels.extend(channels)
        except Exception as exc:
            logger.error("Failed to load local playlist: %s", exc)
            errors.append(f"local: {exc}")

    for url in Config.PLAYLIST_URLS:
        try:
            logger.info("Fetching playlist from %s", url)
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}, proxies=Config.CLASH_PROXIES)
            r.raise_for_status()
            content = r.text
            all_contents.append(content)
            channels = parse_m3u(content)
            logger.info("Parsed %d channels from %s", len(channels), url)
            all_channels.extend(channels)
        except Exception as exc:
            logger.error("Failed to fetch playlist from %s: %s", url, exc)
            errors.append(f"{url}: {exc}")

    if not all_channels:
        # Fallback to cached channels if available
        with _cache_lock:
            cached = _playlist_cache.get("channels")
            if cached:
                logger.warning("All sources failed, returning cached channels")
                return cached, True
        raise Exception("All playlist sources failed: " + "; ".join(errors))

    # Deduplicate by URL
    seen_urls = set()
    deduped = []
    for ch in all_channels:
        url = ch.get("url")
        if url and url not in seen_urls:
            seen_urls.add(url)
            deduped.append(ch)

    # Compute combined hash
    combined = "\n".join(all_contents)
    content_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]

    # Enrich with health data
    for ch in deduped:
        h = get_channel_health(ch.get("url", ""))
        ch["health"] = h
        ch["access_mode"] = h.get("mode", "unknown")
        fmt = h.get("format", "unknown")
        # Fallback: heuristic for known FLV sources
        url_lower = (ch.get("url") or "").lower()
        if fmt == "unknown" and ("iptv.4666888.xyz" in url_lower or url_lower.endswith(".flv")):
            fmt = "FLV"
        ch["format"] = fmt

    with _cache_lock:
        _playlist_cache = {
            "channels": deduped,
            "fetched_at": now,
            "error": "; ".join(errors) if errors else None,
            "hash": content_hash,
        }

    # Persist hash
    _save_playlist_hash(content_hash)

    # Kick off background health checks
    urls = [ch["url"] for ch in deduped if ch.get("url")]
    if urls:
        threading.Thread(target=check_channels_background, args=(urls,), daemon=True).start()

    return deduped, False


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
            current_hash = _playlist_cache.get("hash", "")
        return jsonify({
            "channels": channels,
            "count": len(channels),
            "from_cache": from_cache,
            "cached_at": cached_at,
            "hash": current_hash,
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
    body = resp.content
    # Detect m3u8 by suffix, content-type, or body signature
    is_m3u8 = (
        target.endswith(".m3u8")
        or "mpegurl" in content_type
        or "m3u8" in content_type
        or body.strip().startswith(b"#EXTM3U")
    )

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
