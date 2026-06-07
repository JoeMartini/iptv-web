// Theme management
function getTheme() { return document.documentElement.getAttribute('data-theme') || 'dark'; }
function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    const tc = document.getElementById('themeColor');
    if (tc) tc.setAttribute('content', theme === 'dark' ? '#0a0a0a' : '#f5f5f5');
    const btn = document.getElementById('themeBtn');
    if (btn) btn.textContent = theme === 'dark' ? '🌙' : '☀️';
    try { localStorage.setItem('iptv-theme', theme); } catch (e) {}
}
function toggleTheme() { setTheme(getTheme() === 'dark' ? 'light' : 'dark'); }
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
    if (!localStorage.getItem('iptv-theme')) setTheme(e.matches ? 'dark' : 'light');
});
document.addEventListener('keydown', e => {
    if ((e.key === 't' || e.key === 'T') && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault(); toggleTheme();
    }
});
setTheme(getTheme());

const player = document.getElementById('player');
const groupsEl = document.getElementById('groups');
const nowPlayingEl = document.getElementById('nowPlaying');
const btnShowAll = document.getElementById('btnShowAll');
const toastEl = document.getElementById('toast');
const historyDrawer = document.getElementById('historyDrawer');
const historyOverlay = document.getElementById('historyOverlay');
const historyDrawerBody = document.getElementById('historyDrawerBody');

let hls = null;
let mpegPlayer = null;
let channelsData = [];
let showAllChannels = false;
try {
  showAllChannels = localStorage.getItem('iptv_show_all') === 'true';
} catch (e) {
  console.warn('localStorage unavailable, using default');
}
let currentUrl = '';
let playAttemptTimer = null;
let playChannelName = '';
let currentPlaylistHash = '';
let updateCheckTimer = null;
let updateBanner = null;

updateShowAllUI();

function initLoad() {
  console.log('[IPTV] Loading playlist...');
  loadPlaylist().then(() => {
    console.log('[IPTV] Playlist loaded:', channelsData.length);
    startUpdateChecker();
  }).catch(e => {
    console.error('[IPTV] Playlist load failed:', e.message);
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initLoad);
} else {
  initLoad();
}

// ---------------------------------------------------------------------------
// Keyboard shortcuts
// ---------------------------------------------------------------------------
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeHistoryDrawer();
  }
  if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    showToast('快捷键: [?] 帮助  [Esc] 关闭面板');
  }
});

// ---------------------------------------------------------------------------
// History Drawer (localStorage)
// ---------------------------------------------------------------------------
function getHistory() {
  try {
    return JSON.parse(localStorage.getItem('iptv_history') || '[]');
  } catch (e) { return []; }
}

function saveHistory(history) {
  try { localStorage.setItem('iptv_history', JSON.stringify(history.slice(0, 20))); } catch (e) {}
}

function addToHistory(name, url) {
  let history = getHistory();
  history = history.filter(h => h.url !== url);
  history.unshift({name, url, time: Date.now()});
  saveHistory(history);
}

function openHistoryDrawer() {
  renderHistoryDrawer();
  historyOverlay.classList.add('open');
  historyDrawer.classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeHistoryDrawer() {
  historyOverlay.classList.remove('open');
  historyDrawer.classList.remove('open');
  document.body.style.overflow = '';
}

function clearHistory() {
  if (!confirm('确定要清空所有播放历史吗？')) return;
  try { localStorage.removeItem('iptv_history'); } catch (e) {}
  renderHistoryDrawer();
  showToast('历史记录已清空');
}

function renderHistoryDrawer() {
  if (!historyDrawerBody) return;
  const history = getHistory();
  if (!history.length) {
    historyDrawerBody.innerHTML = '<div class="drawer-empty">暂无播放记录</div>';
    return;
  }
  let html = '';
  for (const h of history) {
    const timeStr = formatTimeAgo(h.time);
    html += `<div class="drawer-item" tabindex="0" role="button" onclick="playChannel('${encodeURIComponent(h.url)}', '${escapeHtml(h.name)}'); closeHistoryDrawer();">
      <div class="drawer-item-logo">${escapeHtml(h.name.charAt(0))}</div>
      <div class="drawer-item-info">
        <div class="drawer-item-name">${escapeHtml(h.name)}</div>
        <div class="drawer-item-time">${timeStr}</div>
      </div>
      <div class="drawer-item-play">▶</div>
    </div>`;
  }
  historyDrawerBody.innerHTML = html;
}

function formatTimeAgo(timestamp) {
  const now = Date.now();
  const diff = now - timestamp;
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return minutes + ' 分钟前';
  if (hours < 24) return hours + ' 小时前';
  if (days < 7) return days + ' 天前';
  const d = new Date(timestamp);
  return (d.getMonth() + 1) + '/' + d.getDate();
}

// ---------------------------------------------------------------------------
// Update checker
// ---------------------------------------------------------------------------
function startUpdateChecker() {
  if (updateCheckTimer) clearInterval(updateCheckTimer);
  updateCheckTimer = setInterval(checkForUpdates, 60000);
}

async function checkForUpdates() {
  try {
    const resp = await fetch('/api/playlist');
    if (!resp.ok) return;
    const data = await resp.json();
    if (!data.hash) return;
    if (!currentPlaylistHash) {
      currentPlaylistHash = data.hash;
      return;
    }
    if (currentPlaylistHash !== data.hash) {
      console.log('[IPTV] Playlist hash changed:', currentPlaylistHash, '->', data.hash);
      showUpdateBanner();
    }
  } catch (e) {
    // silent fail
  }
}

function showUpdateBanner() {
  if (updateBanner) return;
  updateBanner = document.createElement('div');
  updateBanner.className = 'update-banner';
  updateBanner.innerHTML = '播放源已更新，<u>点击刷新</u> 获取最新频道列表';
  updateBanner.onclick = () => window.location.reload();
  document.body.appendChild(updateBanner);
}

function toggleShowAll() {
  showAllChannels = !showAllChannels;
  try { localStorage.setItem('iptv_show_all', showAllChannels); } catch (e) {}
  updateShowAllUI();
  renderGroups();
}

function updateShowAllUI() {
  btnShowAll.textContent = showAllChannels ? '可播放' : '全部';
}

function loadPlaylistWithXHR(forceRefresh, callback) {
  const url = '/api/playlist' + (forceRefresh ? '?refresh=true' : '');
  const xhr = new XMLHttpRequest();
  xhr.open('GET', url, true);
  xhr.timeout = 10000;
  xhr.onreadystatechange = function() {
    if (xhr.readyState === 4) {
      if (xhr.status === 200) {
        try {
          const data = JSON.parse(xhr.responseText);
          if (data.error) throw new Error(data.error);
          channelsData = data.channels || [];
          if (data.hash) currentPlaylistHash = data.hash;
          renderGroups();
          showToast('已加载 ' + channelsData.length + ' 个频道' + (data.from_cache ? ' (缓存)' : ''));
          if (callback) callback(null, data);
        } catch (e) { if (callback) callback(e); }
      } else { if (callback) callback(new Error('HTTP ' + xhr.status)); }
    }
  };
  xhr.onerror = function() { if (callback) callback(new Error('网络请求失败')); };
  xhr.ontimeout = function() { if (callback) callback(new Error('请求超时')); };
  xhr.send();
}

async function loadPlaylist(forceRefresh) {
  showToast(forceRefresh ? '正在刷新播放列表…' : '正在加载播放列表…');
  groupsEl.innerHTML = '<div class="empty">正在加载频道列表…</div>';

  const timeoutId = setTimeout(() => {
    if (!channelsData.length) {
      groupsEl.innerHTML = '<div class="empty"><div style="font-size:32px;margin-bottom:12px">⏱</div><div>加载超时</div><div class="retry-wrap"><button class="btn" onclick="loadPlaylist(false)">重试</button></div></div>';
    }
  }, 5000);

  let lastError = null;
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      const url = '/api/playlist' + (forceRefresh ? '?refresh=true' : '');
      const resp = await fetch(url);
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      if (data.error) throw new Error(data.error);
      channelsData = data.channels || [];
      if (data.hash) currentPlaylistHash = data.hash;
      renderGroups();
      clearTimeout(timeoutId);
      showToast('已加载 ' + channelsData.length + ' 个频道' + (data.from_cache ? ' (缓存)' : ''));
      return;
    } catch (e) {
      lastError = e;
      if (attempt < 2) await new Promise(r => setTimeout(r, 1500));
    }
  }

  try {
    await new Promise((resolve, reject) => {
      loadPlaylistWithXHR(forceRefresh, (err, data) => { if (err) reject(err); else resolve(data); });
    });
    clearTimeout(timeoutId);
    return;
  } catch (e) { lastError = e; }

  clearTimeout(timeoutId);
  showToast('加载失败: ' + lastError.message);
  groupsEl.innerHTML = '<div class="empty"><div style="font-size:32px;margin-bottom:12px">×</div><div>无法加载频道列表</div><div class="retry-wrap"><button class="btn" onclick="loadPlaylist(false)">重试</button></div></div>';
}

function renderGroups() {
  if (!channelsData.length) return;
  const filtered = showAllChannels ? channelsData.filter(ch => ch.access_mode !== 'none') : channelsData;
  const groups = {};
  for (const ch of filtered) {
    const g = ch.group || '未分组';
    if (!groups[g]) groups[g] = [];
    groups[g].push(ch);
  }
  let html = '';
  for (const [groupName, channels] of Object.entries(groups)) {
    html += `<div class="group"><div class="group-title" onclick="toggleGroup(this)">${escapeHtml(groupName)} (${channels.length})</div><div class="grid">`;
    for (const ch of channels) html += renderChannelCard(ch);
    html += '</div></div>';
  }
  groupsEl.innerHTML = html || '<div class="empty">暂无频道</div>';
}

function renderChannelCard(ch) {
  const isPlaying = ch.url === currentUrl;
  const logoHtml = ch.logo
    ? `<div class="logo-wrap"><img class="logo" src="${proxyUrl(ch.logo)}" alt="" loading="lazy" onerror="this.style.display='none';this.parentElement.querySelector('.logo-placeholder').style.display='flex';"><div class="logo-placeholder" style="display:none">${escapeHtml(ch.name.charAt(0))}</div></div>`
    : `<div class="logo-wrap"><div class="logo-placeholder">${escapeHtml(ch.name.charAt(0))}</div></div>`;
  const h = ch.health || {};
  const d = h.direct || {status: 'unknown'};
  const p = h.proxy || {status: 'unknown'};
  const mode = ch.access_mode || 'unknown';
  let modeLabel = '';
  if (mode === 'direct') modeLabel = '<span style="color:#34d399;font-size:9px">直</span>';
  else if (mode === 'proxy') modeLabel = '<span style="color:#fbbf24;font-size:9px">代</span>';
  else if (mode === 'none') modeLabel = '<span style="color:#f87171;font-size:9px">×</span>';
  else modeLabel = '<span style="color:#666;font-size:9px">?</span>';
  const fmt = ch.format || 'unknown';
  const fmtLabel = fmt === 'FLV' ? '<span style="background:#b45309;color:#fff;font-size:9px;padding:1px 4px;border-radius:3px;margin-left:3px">FLV</span>' : '';
  const healthHtml = `<div class="health">${modeLabel}<span class="health-dot health-${d.status}"></span><span class="health-dot health-${p.status}"></span></div>`;
  return `<div class="channel${isPlaying ? ' playing' : ''}" data-url="${encodeURIComponent(ch.url)}" data-format="${fmt}" tabindex="0" role="button" onclick="playChannel('${encodeURIComponent(ch.url)}', '${escapeHtml(ch.name)}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();playChannel('${encodeURIComponent(ch.url)}','${escapeHtml(ch.name)}')}">${logoHtml}<div class="name">${escapeHtml(ch.name)}${fmtLabel}</div>${healthHtml}</div>`;
}

function toggleGroup(header) { header.parentElement.classList.toggle('collapsed'); }

function markPlaying() {
  document.querySelectorAll('.channel').forEach(el => {
    el.classList.toggle('playing', decodeURIComponent(el.dataset.url) === currentUrl);
  });
}

// ---------------------------------------------------------------------------
// Format detection
// ---------------------------------------------------------------------------
function detectStreamFormat(url, formatHint) {
  if (formatHint === 'FLV') return 'flv';
  if (!url) return 'unknown';
  const lower = url.toLowerCase();
  if (lower.endsWith('.m3u8') || lower.includes('.m3u8')) return 'hls';
  if (lower.endsWith('.flv')) return 'flv';
  // Heuristic: iptv.4666888.xyz sources are typically FLV
  if (lower.includes('iptv.4666888.xyz')) return 'flv';
  // Default to HLS for most IPTV streams
  return 'hls';
}

// ---------------------------------------------------------------------------
// FLV support: lazy-load mpegts.js
// ---------------------------------------------------------------------------
let mpegtsLoaded = false;
function loadMpegtsJs() {
  return new Promise((resolve, reject) => {
    if (typeof mpegts !== 'undefined') { mpegtsLoaded = true; resolve(); return; }
    if (mpegtsLoaded) { resolve(); return; }
    const script = document.createElement('script');
    script.src = '/static/js/mpegts.js';
    script.onload = () => { mpegtsLoaded = true; resolve(); };
    script.onerror = () => reject(new Error('无法加载 mpegts.js'));
    document.head.appendChild(script);
  });
}

function playChannel(encodedUrl, name) {
  const url = decodeURIComponent(encodedUrl);
  if (!url) return;
  currentUrl = url;
  playChannelName = name || 'Unknown';
  nowPlayingEl.textContent = '正在播放: ' + playChannelName;
  markPlaying();
  addToHistory(name, url);
  if (url.startsWith('rtmp://')) {
    showToast('RTMP 不支持浏览器直接播放，请使用 VLC: ' + name);
    player.pause();
    return;
  }
  // Get format hint from channel data if available
  const ch = channelsData.find(c => c.url === url);
  const formatHint = ch ? ch.format : null;
  const format = detectStreamFormat(url, formatHint);
  if (format === 'flv') {
    playFlv(url);
  } else {
    _doPlayHls(url, false);
  }
}

// ---------------------------------------------------------------------------
// HLS playback (existing logic)
// ---------------------------------------------------------------------------
function _doPlayHls(url, forceProxy) {
  if (hls) { hls.destroy(); hls = null; }
  if (mpegPlayer) { mpegPlayer.destroy(); mpegPlayer = null; }
  if (playAttemptTimer) { clearTimeout(playAttemptTimer); playAttemptTimer = null; }

  const proxyUrl = '/api/proxy?url=' + encodeURIComponent(url) + (forceProxy ? '&force_proxy=true' : '');
  console.log('[IPTV] HLS play attempt:', forceProxy ? 'proxy' : 'direct', proxyUrl);

  let hasStarted = false;

  if (Hls.isSupported()) {
    hls = new Hls({enableWorker: true});
    hls.loadSource(proxyUrl);
    hls.attachMedia(player);
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      hasStarted = true;
      player.play().catch(() => {});
      showToast('▶ ' + playChannelName);
    });
    hls.on(Hls.Events.ERROR, (e, data) => {
      if (data.fatal) {
        console.error('[IPTV] HLS fatal:', data.type, data.details);
        if (!forceProxy && !hasStarted) {
          showToast('直连失败，尝试代理…');
          _doPlayHls(url, true);
        } else {
          showToast('播放失败: ' + (data.type || 'unknown'));
        }
      }
    });
    playAttemptTimer = setTimeout(() => {
      if (!hasStarted) {
        if (!forceProxy) { showToast('直连超时，尝试代理…'); _doPlayHls(url, true); }
        else { showToast('频道不可用'); }
      }
    }, 15000);
  } else if (player.canPlayType('application/vnd.apple.mpegurl')) {
    player.src = proxyUrl;
    player.addEventListener('loadedmetadata', () => { hasStarted = true; player.play().catch(() => {}); showToast('▶ ' + playChannelName); });
    playAttemptTimer = setTimeout(() => { if (!hasStarted && !forceProxy) { showToast('直连超时，尝试代理…'); _doPlayHls(url, true); } }, 15000);
  } else {
    player.src = proxyUrl;
    player.play().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// FLV playback (mpegts.js)
// ---------------------------------------------------------------------------
async function playFlv(url) {
  if (hls) { hls.destroy(); hls = null; }
  if (mpegPlayer) { mpegPlayer.destroy(); mpegPlayer = null; }
  if (playAttemptTimer) { clearTimeout(playAttemptTimer); playAttemptTimer = null; }

  try {
    showToast('正在加载 FLV 播放器…');
    await loadMpegtsJs();

    const proxyUrl = '/api/proxy?url=' + encodeURIComponent(url) + '&force_proxy=true';
    console.log('[IPTV] FLV play:', proxyUrl);

    if (!mpegts.getFeatureList().mseLivePlayback) {
      showToast('当前浏览器不支持 FLV 播放');
      return;
    }

    mpegPlayer = mpegts.createPlayer({
      type: 'flv',
      isLive: true,
      url: proxyUrl,
      cors: true,
      enableWorker: true,
      enableStashBuffer: false,
      stashInitialSize: 128,
    });
    mpegPlayer.attachMediaElement(player);

    let hasStarted = false;

    mpegPlayer.on(mpegts.Events.ERROR, (errorType, errorDetail, errorInfo) => {
      console.error('[IPTV] FLV error:', errorType, errorDetail, errorInfo);
      if (!hasStarted) {
        showToast('FLV 播放失败: ' + (errorDetail || errorType));
      }
    });

    mpegPlayer.on(mpegts.Events.MEDIA_INFO, (mediaInfo) => {
      console.log('[IPTV] FLV media info:', mediaInfo);
      hasStarted = true;
      showToast('▶ ' + playChannelName);
    });

    mpegPlayer.on(mpegts.Events.VIDEO_DECODED_START, () => {
      console.log('[IPTV] FLV video decoded');
      hasStarted = true;
    });

    mpegPlayer.load();
    mpegPlayer.play();

    playAttemptTimer = setTimeout(() => {
      if (!hasStarted) {
        showToast('FLV 频道不可用');
      }
    }, 15000);

  } catch (e) {
    console.error('[IPTV] FLV load failed:', e);
    showToast('加载 FLV 播放器失败: ' + e.message);
  }
}

function proxyUrl(url) {
  if (!url) return url;
  return '/api/proxy?url=' + encodeURIComponent(url);
}

let toastTimer;
function showToast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove('show'), 3000);
}

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

player.addEventListener('error', (e) => {
  const err = player.error;
  if (err) {
    let msg = '播放错误';
    switch(err.code) { case 1: msg = '已中止'; break; case 2: msg = '网络错误，请刷新重试'; break; case 3: msg = '解码错误'; break; case 4: msg = '格式不支持'; break; }
    showToast('错误: ' + msg);
  }
});
