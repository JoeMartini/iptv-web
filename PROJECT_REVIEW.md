# IPTV Web Player - 项目回顾与总结

> 项目周期：2026-05-15 ~ 2026-05-17
> 最终版本：v11
> 部署状态：生产环境运行中

---

## 一、项目目标

搭建一个基于 GNTV.m3u 的 Web IPTV 在线播放服务，支持：
- 动态加载 M3U 播放列表
- 点击切换频道，HLS 视频播放
- 服务端代理模式（解决跨域/地区限制/Mixed Content）
- 移动端兼容的响应式 UI
- 频道健康状态可视化
- 通过现有 OpenResty 反代提供 HTTPS 访问

---

## 二、功能清单

### 核心功能
| 功能 | 说明 |
|------|------|
| M3U 解析 | 解析 `#EXTINF` 元数据，提取频道名、分组、台标、URL |
| HLS 播放 | 基于 hls.js 的 HLS 流媒体播放，支持 native HLS (Safari) |
| 服务端代理 | `/api/proxy` 代理所有 HTTP/HTTPS 请求，重写 m3u8 内相对路径 |
| 代理自动切换 | 15 秒未触发 `MANIFEST_PARSED` 自动切代理；HLS fatal 错误立即 fallback |
| 健康检查 | 双模式检测（直连 + Clash 代理），返回 green/yellow/red/unknown |
| 状态可视化 | 频道卡片显示"直"/"代"/"×"/"?"标记 + 双色健康圆点 |
| 缓存系统 | 内存缓存（5 分钟 TTL）+ 磁盘持久化缓存（`.health_cache.json`） |
| 版本号检测 | Playlist SHA256 哈希作为版本号，前端 60 秒轮询，更新时弹横幅提示 |
| 分组折叠 | 按 `group-title` 分组，支持点击展开/折叠 |
| 可播放/全部切换 | 过滤 `access_mode === 'none'` 的不可播放频道 |

### 工程功能
| 功能 | 说明 |
|------|------|
| Docker 容器化 | python:3.11-slim，非 root 用户 (`iptv:iptv`)，HEALTHCHECK |
| OpenResty 反代 | HTTPS 域名 `iptv.home.martini.wang:50443` |
| 速率限制 | 500 请求/60 秒，覆盖 HLS TS 分片并发 |
| 安全加固 | URL scheme 校验（仅 http/https）、错误信息截断脱敏 |
| 开源标准 | MIT 许可证、中英 README、pytest 测试、.gitignore |

---

## 三、迭代路径

```
Phase 1: MVP (v1)
  ├── 单文件 Flask 应用 (app.py)
  ├── 内嵌 HTML/CSS/JS
  ├── 基础播放 + 代理
  └── 部署到 127.0.0.1:5005

Phase 2: 代理与播放修复 (v2)
  ├── video 标签增加 crossorigin="anonymous"
  ├── m3u8 相对路径重写为绝对代理 URL
  ├── 默认强制代理 HTTP 源
  └── 修复 headers 迭代 bug

Phase 3: 健康检查 (v3)
  ├── 后台双模式检测（直连/代理）
  ├── 状态圆点 CSS
  ├── 频道卡片标记
  └── 播放成功上报（/api/report_play）

Phase 4: 缓存与性能 (v4)
  ├── Playlist 内存缓存（5 分钟 TTL）
  ├── 缓存加载按钮
  └── 强制拉取按钮

Phase 5: 开源重构 (v5)
  ├── 前后端分离（templates/ + static/）
  ├── config.py 集中配置
  ├── 速率限制 500/60s（修复 429）
  └── pytest 11 个测试用例

Phase 6: 格式兼容修复 (v6~v8)
  ├── gzip M3U8 解压（凤凰中文等）
  ├── 嵌套 M3U8 递归修复（TVBS 亚洲）
  ├── 魔数字节格式检测（FLV/MP4/MKV/TS）
  └── FLV 标记为不支持

Phase 7: 版本号与持久化 (v9~v11)
  ├── Playlist SHA256 哈希版本号
  ├── 前端 60 秒轮询更新检测
  ├── 健康检查缓存持久化到磁盘
  ├── 容器内 cache 目录 + 宿主机挂载
  └── 修复 UnboundLocalError (_playlist_cache)
```

---

## 四、关键问题与解决办法

### 1. 磁盘空间不足
- **现象**：根分区使用率 46%，Docker 构建失败
- **解决**：删除 `/app/ollama_models/` (~54GB)、`/app/Windows/` (~24GB)、Docker builder cache (~25GB)
- **结果**：使用率降至 14%，可用 386GB

### 2. HTTPS 页面请求 HTTP 源 → Mixed Content 拦截
- **现象**：浏览器拒绝加载 HTTP 视频流
- **解决**：前端 `proxyUrl()` 自动强制代理 HTTP 源；默认代理状态改为 `!== 'false'`（默认开启）
- **教训**：HTTPS 站点的任何子资源都必须是 HTTPS 或同源代理

### 3. 视频标签缺少 crossorigin
- **现象**：HLS 播放失败，CORS 错误
- **解决**：`<video crossorigin="anonymous">`
- **教训**：HLS 需要 CORS 才能通过 MediaSource Extensions 播放

### 4. m3u8 内相对路径未重写
- **现象**：TS 分片请求 404（请求的是相对路径而非代理 URL）
- **解决**：后端 `rewrite_m3u8()` 将所有非注释行重写为 `/api/proxy?url=...`
- **教训**：代理 m3u8 必须递归重写所有内部 URL

### 5. Rate Limit 429 导致播放中断
- **现象**：播放几秒后全部频道变红，前端报 429
- **根因**：60 请求/60 秒限制过于严格，HLS TS 分片并发触发
- **解决**：提升至 500/60 秒
- **教训**：HLS 播放的并发 TS 请求远高于预期，速率限制需按播放器行为设计

### 6. gzip 压缩的 M3U8 被误判
- **现象**：凤凰中文、凤凰资讯显示为 non-HLS，播放失败
- **根因**：cdn6.163189.xyz 返回 gzip 压缩体，Content-Type: text/plain
- **解决**：新增 `_decompress_if_gzip()`，检测前自动解压
- **教训**：不能信任 Content-Type，必须校验响应体签名（#EXTM3U）

### 7. 嵌套 M3U8 递归错误
- **现象**：TVBS 亚洲解析失败，TS 二进制数据被拼接为 URL
- **根因**：master playlist → chunklist.m3u8 → TS，递归时未验证子响应是否为 M3U8
- **解决**：递归前验证响应体以 `#EXTM3U` 开头，否则视为 TS 层终止递归
- **教训**：多层 playlist 必须逐层验证格式，不能假设 URL 后缀正确

### 8. FLV 格式浏览器不支持
- **现象**：TVBS精采、华艺中文显示为可用但播放失败
- **根因**：iptv.4666888.xyz 返回 video/x-flv，浏览器 `<video>` 原生不支持
- **解决**：`_detect_stream_format()` 基于魔数字节识别 FLV，标记为 `none`
- **教训**：Content-Type 可能是错的（video/x-flv 也可能被误标），魔数字节才是金标准

### 9. Linux Docker 不支持 host.docker.internal
- **现象**：容器内代理地址不可解析
- **解决**：使用宿主机真实 IP `192.168.1.146:7890`
- **教训**：`host.docker.internal` 仅在 Docker Desktop（Mac/Win）有效

### 10. UnboundLocalError: _playlist_cache
- **现象**：v9 部署后 `/api/playlist` 返回 502
- **根因**：`fetch_playlist()` 内对全局变量 `_playlist_cache` 赋值，缺 `global` 声明
- **解决**：添加 `global _playlist_cache`
- **教训**：Python 中函数内对全局变量赋值必须显式声明 global

### 11. 容器内非 root 用户无法写缓存
- **现象**：`.health_cache.json` 无法生成
- **根因**：`USER iptv` 后无写权限，`/app` 目录属主为 root
- **解决**：Dockerfile 中 `mkdir -p /app/cache && chown iptv:iptv /app/cache`
- **教训**：非 root 容器必须预先创建并授权所需目录

---

## 五、错误与教训汇总

| # | 错误/问题 | 根因 | 教训 |
|---|-----------|------|------|
| 1 | 磁盘空间不足 | 长期未清理大文件和 Docker cache | 定期审计磁盘，设置监控告警 |
| 2 | Mixed Content 拦截 | HTTPS 页面加载 HTTP 视频 | 代理模式默认开启，所有子资源走 HTTPS/代理 |
| 3 | 429 Rate Limit | HLS TS 分片并发远超预期 | 速率限制要按实际播放器并发设计 |
| 4 | gzip M3U8 误判 | 依赖 Content-Type 而非响应体 | 永远校验响应体签名，处理 gzip 压缩 |
| 5 | 嵌套 M3U8 递归崩 | 未验证子响应格式 | 每层递归前验证 #EXTM3U 签名 |
| 6 | FLV 误判为可用 | 未做流格式检测 | 魔数字节 > Content-Type > URL 后缀 |
| 7 | host.docker.internal 无效 | Linux Docker 不支持 | 生产环境使用真实 IP 或 docker network |
| 8 | UnboundLocalError | 缺 global 声明 | Python 全局变量赋值必须显式声明 |
| 9 | 容器缓存权限 | 非 root 用户无写权限 | Dockerfile 中预先创建并 chown 所需目录 |
| 10 | 配置串扰 | nginx 新增 default_server 冲突 | 检查现有 catch-all，避免配置冲突 |

---

## 六、架构与部署

```
┌─────────────────────────────────────────────────────────────┐
│                      用户浏览器                                │
│         https://iptv.home.martini.wang:50443                │
└────────────────────────┬────────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │   1Panel OpenResty   │  (host 网络模式)
              │   端口 50443 (HTTPS) │
              └──────────┬──────────┘
                         │ proxy_pass
              ┌──────────▼──────────┐
              │   127.0.0.1:5005    │
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │   Docker: iptv-web  │
              │   端口 5005         │
              │   镜像: v11         │
              └──────────┬──────────┘
                         │
              ┌──────────┴──────────┐
              │   Clash Proxy       │
              │   192.168.1.146:7890 │
              └─────────────────────┘
```

### 生产命令
```bash
cd /app/iptv-web
docker build -t iptv-web:v11 .
docker run -d --name iptv-web -p 5005:5005 \
  -v /app/iptv-web/cache:/app/cache \
  -e CLASH_PROXY=http://192.168.1.146:7890 \
  iptv-web:v11
```

---

## 七、核心代码文件

| 文件 | 说明 | 行数 |
|------|------|------|
| `app.py` | Flask 后端：路由、代理、健康检查、缓存 | ~620 |
| `config.py` | 集中配置：URL、超时、速率限制、代理 | ~80 |
| `templates/index.html` | Jinja2 模板，播放器 + 频道网格 | ~150 |
| `static/js/app.js` | 前端逻辑：播放、健康检测、版本轮询 | ~260 |
| `static/css/style.css` | 响应式样式，移动端优先 | ~200 |
| `tests/test_app.py` | pytest 测试：M3U 解析、代理、路由、限流 | ~150 |
| `Dockerfile` | 非 root 容器，HEALTHCHECK | ~30 |
| `docker-compose.yml` | 一键部署 | ~20 |

---

## 八、待办/改进方向

1. **FLV 支持**：集成 flv.js 或 mpegts.js，让 TVBS精采等 FLV 频道可播放
2. **EPG 节目单**：对接电子节目单 API，显示当前/下一节目
3. **收藏功能**：localStorage 保存用户收藏频道
4. **搜索功能**：频道名称实时过滤
5. **播放历史**：记录最近播放的 N 个频道
6. **WebSocket 推送**：替代轮询，实时推送健康状态更新
7. **多源聚合**：支持多个 M3U 源合并
8. **自动源切换**：同一频道多个 URL 时，自动选择最优源

---

## 九、一句话总结

> 一个从单文件 Flask 脚本迭代到 Docker 容器化、支持双模式健康检查、自动代理切换、版本号检测和缓存持久化的开源级 Web IPTV 播放器，在修复了 gzip M3U8、嵌套 playlist、FLV 格式、速率限制和容器权限等 10+ 个坑后，稳定运行在生产环境。
