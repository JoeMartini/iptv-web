"""
IPTV Web Player - Configuration

All settings are loaded from environment variables with sensible defaults.
"""

import os
from typing import Optional


class Config:
    """Application configuration."""

    # Server
    HOST: str = os.environ.get("IPTV_HOST", "0.0.0.0")
    PORT: int = int(os.environ.get("IPTV_PORT", "5005"))
    DEBUG: bool = os.environ.get("IPTV_DEBUG", "").lower() == "true"

    # Playlist
    PLAYLIST_URL: str = os.environ.get(
        "IPTV_PLAYLIST_URL",
        "https://raw.githubusercontent.com/YueChan/Live/main/GNTV.m3u",
    )
    PLAYLIST_CACHE_TTL: int = int(os.environ.get("IPTV_CACHE_TTL", "300"))
    HEALTH_CACHE_TTL: int = int(os.environ.get("IPTV_HEALTH_TTL", "300"))

    # Proxy
    CLASH_PROXY: Optional[str] = os.environ.get('CLASH_PROXY', 'http://192.168.1.146:7890')
    CLASH_PROXIES: Optional[dict] = None
    if CLASH_PROXY:
        CLASH_PROXIES = {"http": CLASH_PROXY, "https": CLASH_PROXY}
    NO_PROXIES: Optional[dict] = None

    # Security
    PROXY_MAX_CONTENT_LENGTH: int = int(
        os.environ.get("IPTV_PROXY_MAX_SIZE", "104857600")
    )  # 100MB
    PROXY_TIMEOUT: int = int(os.environ.get("IPTV_PROXY_TIMEOUT", "60"))
    RATE_LIMIT_ENABLED: bool = os.environ.get("IPTV_RATE_LIMIT", "true").lower() == "true"
    RATE_LIMIT_REQUESTS: int = int(os.environ.get("IPTV_RATE_LIMIT_REQ", "500"))
    RATE_LIMIT_WINDOW: int = int(os.environ.get("IPTV_RATE_LIMIT_WINDOW", "60"))

    # Health check
    HEALTH_CHECK_TIMEOUT: tuple = (
        int(os.environ.get("IPTV_HEALTH_CONNECT_TIMEOUT", "2")),
        int(os.environ.get("IPTV_HEALTH_READ_TIMEOUT", "4")),
    )
    HEALTH_CHECK_WORKERS: int = int(os.environ.get("IPTV_HEALTH_WORKERS", "20"))

    # Logging
    LOG_LEVEL: str = os.environ.get("IPTV_LOG_LEVEL", "INFO")

    @classmethod
    def validate(cls) -> list[str]:
        """Validate configuration and return list of issues."""
        issues = []
        if not cls.PLAYLIST_URL.startswith(("http://", "https://")):
            issues.append("PLAYLIST_URL must be an HTTP(S) URL")
        if cls.PORT < 1 or cls.PORT > 65535:
            issues.append("PORT must be between 1 and 65535")
        if cls.PLAYLIST_CACHE_TTL < 0:
            issues.append("CACHE_TTL must be non-negative")
        return issues
