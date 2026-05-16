const player = document.getElementById('player');
const groupsEl = document.getElementById('groups');
const nowPlayingEl = document.getElementById('nowPlaying');
const btnShowAll = document.getElementById('btnShowAll');
const toastEl = document.getElementById('toast');

let hls = null;
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

updateShowAllUI();

function initLoad() {
  console.log('[IPTV] Loading playlist...');
  loadPlaylist().then(() => {
    console.log('[IPTV] Playlist loaded:', channelsData.length);
  }).catch(e => {
    console.error('[IPTV] Playlist load failed:', e.message);
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initLoad);
} else {
  initLoad();
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
          renderGroups();
          showToast('✅ Loaded ' + channelsData.length + ' channels' + (data.from_cache ? ' (cached)' : ''));
          if (callback) callback(null, data);
        } catch (e) { if (callback) callback(e); }
      } else { if (callback) callback(new Error('HTTP ' + xhr.status)); }
    }
  };
  xhr.onerror = function() { if (callback) callback(new Error('XHR network error')); };
  xhr.ontimeout = function() { if (callback) callback(new Error('XHR timeout')); };
  xhr.send();
}

async function loadPlaylist(forceRefresh) {
  showToast(forceRefresh ? 'Refreshing playlist…' : 'Loading playlist…');
  groupsEl.innerHTML = '<div class="empty">Loading channels…</div>';

  const timeoutId = setTimeout(() => {
    if (!channelsData.length) {
      groupsEl.innerHTML = '<div class="empty"><div style="font-size:32px;margin-bottom:12px">⏱️</div><div>Loading timeout</div><button class="btn" style="margin-top:16px" onclick="loadPlaylist(false)">Retry</button></div>';
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
      renderGroups();
      clearTimeout(timeoutId);
      showToast('✅ Loaded ' + channelsData.length + ' channels' + (data.from_cache ? ' (cached)' : ''));
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
  showToast('❌ Failed: ' + lastError.message);
  groupsEl.innerHTML = '<div class="empty"><div style="font-size:32px;margin-bottom:12px">😵</div><div>Failed to load channels</div><button class="btn" style="margin-top:16px" onclick="loadPlaylist(false)">Retry</button></div>';
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
  groupsEl.innerHTML = html || '<div class="empty">No channels</div>';
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
  if (mode === 'direct') modeLabel = '<span style="color:#4caf50;font-size:9px">直</span>';
  else if (mode === 'proxy') modeLabel = '<span style="color:#ff9800;font-size:9px">代</span>';
  else if (mode === 'none') modeLabel = '<span style="color:#f44336;font-size:9px">×</span>';
  const healthHtml = `<div class="health">${modeLabel}<span class="health-dot health-${d.status}"></span><span class="health-dot health-${p.status}"></span></div>`;
  return `<div class="channel${isPlaying ? ' playing' : ''}" data-url="${encodeURIComponent(ch.url)}" onclick="playChannel('${encodeURIComponent(ch.url)}', '${escapeHtml(ch.name)}')">${logoHtml}<div class="name">${escapeHtml(ch.name)}</div>${healthHtml}</div>`;
}

function toggleGroup(header) { header.parentElement.classList.toggle('collapsed'); }

function markPlaying() {
  document.querySelectorAll('.channel').forEach(el => {
    el.classList.toggle('playing', decodeURIComponent(el.dataset.url) === currentUrl);
  });
}

function playChannel(encodedUrl, name) {
  const url = decodeURIComponent(encodedUrl);
  if (!url) return;
  currentUrl = url;
  playChannelName = name || 'Unknown';
  nowPlayingEl.textContent = 'Playing: ' + playChannelName;
  markPlaying();
  if (url.startsWith('rtmp://')) {
    showToast('❌ RTMP not supported in browser, use VLC: ' + name);
    player.pause();
    return;
  }
  _doPlay(url, false);
}

function _doPlay(url, forceProxy) {
  if (hls) { hls.destroy(); hls = null; }
  if (playAttemptTimer) { clearTimeout(playAttemptTimer); playAttemptTimer = null; }

  const proxyUrl = '/api/proxy?url=' + encodeURIComponent(url) + (forceProxy ? '&force_proxy=true' : '');
  console.log('[IPTV] Play attempt:', forceProxy ? 'proxy' : 'direct', proxyUrl);

  let hasStarted = false;

  if (Hls.isSupported()) {
    hls = new Hls({enableWorker: true});
    hls.loadSource(proxyUrl);
    hls.attachMedia(player);
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      hasStarted = true;
      player.play().catch(() => {});
      showToast('▶️ ' + playChannelName);
    });
    hls.on(Hls.Events.ERROR, (e, data) => {
      if (data.fatal) {
        console.error('[IPTV] HLS fatal:', data.type, data.details);
        if (!forceProxy && !hasStarted) {
          showToast('⚠️ Direct failed, trying proxy…');
          _doPlay(url, true);
        } else {
          showToast('❌ Playback failed: ' + (data.type || 'unknown'));
        }
      }
    });
    playAttemptTimer = setTimeout(() => {
      if (!hasStarted) {
        if (!forceProxy) { showToast('⏱️ Direct timeout, trying proxy…'); _doPlay(url, true); }
        else { showToast('❌ Channel unavailable'); }
      }
    }, 15000);
  } else if (player.canPlayType('application/vnd.apple.mpegurl')) {
    player.src = proxyUrl;
    player.addEventListener('loadedmetadata', () => { hasStarted = true; player.play().catch(() => {}); showToast('▶️ ' + playChannelName); });
    playAttemptTimer = setTimeout(() => { if (!hasStarted && !forceProxy) { showToast('⏱️ Direct timeout, trying proxy…'); _doPlay(url, true); } }, 15000);
  } else {
    player.src = proxyUrl;
    player.play().catch(() => {});
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
    let msg = 'Playback error';
    switch(err.code) { case 1: msg = 'Aborted'; break; case 2: msg = 'Network error, try refresh'; break; case 3: msg = 'Decode error'; break; case 4: msg = 'Format not supported'; break; }
    showToast('❌ ' + msg);
  }
});
