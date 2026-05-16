# IPTV Web Player

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](Dockerfile)

A lightweight, self-hosted web-based IPTV player with HLS support, server-side proxy, health checking, and automatic proxy fallback.

一个轻量级、自托管的 Web IPTV 播放器，支持 HLS 播放、服务端代理、健康检查和自动代理回退。

---

## Features | 特性

- 📺 **M3U Playlist Support** — Load IPTV channels from any M3U URL
- 🔄 **Smart Proxy** — Server-side proxy with optional Clash/SOCKS5 support
- ⚡ **Auto Fallback** — 15-second timeout, automatically switches to proxy if direct connection fails
- 🏥 **Health Checks** — Background health monitoring for all channels (direct + proxy)
- 📱 **Mobile First** — Responsive design, touch-friendly, PWA-ready
- 🐳 **Docker Ready** — One-command deployment with Docker Compose
- 🔒 **Rate Limiting** — Built-in protection against abuse
- 🎨 **Dark Theme** — Easy on the eyes

---

## Quick Start | 快速开始

### Docker (Recommended)

```bash
git clone <repo-url>
cd iptv-web
docker compose up -d
```

Visit `http://localhost:5005`

### Manual

```bash
pip install -r requirements.txt
python3 app.py
```

---

## Configuration | 配置

All settings are controlled via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `IPTV_HOST` | `0.0.0.0` | Bind address |
| `IPTV_PORT` | `5005` | Bind port |
| `IPTV_PLAYLIST_URL` | `https://raw.githubusercontent.com/YueChan/Live/main/GNTV.m3u` | M3U playlist URL |
| `IPTV_CACHE_TTL` | `300` | Playlist cache TTL (seconds) |
| `CLASH_PROXY` | — | HTTP proxy URL (e.g. `http://127.0.0.1:7890`) |
| `IPTV_RATE_LIMIT` | `true` | Enable rate limiting |
| `IPTV_LOG_LEVEL` | `INFO` | Logging level |

---

## Architecture | 架构

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│   Browser   │────▶│  OpenResty   │────▶│   iptv-web      │
│  (hls.js)   │◀────│   (HTTPS)    │◀────│   (Flask)       │
└─────────────┘     └──────────────┘     └─────────────────┘
                                                  │
                    ┌──────────────┐             │
                    │   Clash      │◀────────────┘
                    │  (optional)  │   (proxy fallback)
                    └──────────────┘
```

---

## API Endpoints | API 接口

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main application page |
| `/api/playlist` | GET | Fetch parsed playlist (`?refresh=true` to force) |
| `/api/proxy?url=...` | GET | Proxy any HTTP resource (`?force_proxy=true` for Clash) |
| `/api/health?url=...` | GET | Batch health check |

---

## Development | 开发

```bash
# Run tests
pytest tests/

# Type checking
mypy app.py

# Linting
ruff check .
```

---

## License | 许可证

[MIT](LICENSE)

---

> ⚠️ **Disclaimer**: This project is for personal/educational use only. Respect copyright and local laws when using IPTV streams.
