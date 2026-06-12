// MATRIX-M Sidepanel Script

/** Windows: register-server-protocol.reg 登録後、cmd /k で run_server を起動 */
const MATRIX_NEO_SERVER_LAUNCH_URL = 'matrixneo://run-server/';

/** 既定は 127.0.0.1（Windows で localhost→::1 になりサーバーに届かないことがある） */
let serverUrl = 'http://127.0.0.1:6850';
let authToken = '';
let detectedVideos = new Map();
let downloadTasks = new Map();
let selectedQualities = new Map();
let savedList = [];
let sseConnection = null;
/** @type {Set<string>} keys currently submitting a download */
const pendingDownloadKeys = new Set();
let sequentialQueueRunning = false;
let serverReachable = false;
let serverWasReachable = false;
let thumbProxyInFlight = false;
let thumbProxyQueued = false;
let thumbProxyWarnedOffline = false;
/** @type {Set<string>} task_ids already written to downloadHistory */
const completedTaskHistoryIds = new Set();
let sseRetryMs = 3000;
const SSE_RETRY_MAX_MS = 60000;
const THUMB_PROXY_CONCURRENCY = 2;
const FETCH_TIMEOUT_MS = 12000;

// Utility functions are loaded from utils.js

function normalizeServerUrl(url) {
    const u = (url || '').trim() || 'http://127.0.0.1:6850';
    return u.replace(/^http:\/\/localhost\b/i, 'http://127.0.0.1').replace(/\/$/, '');
}

function videoListKey(video) {
    if (!video || !video.url) return '';
    return video.url.replace(/\/(1080p|720p|480p|360p|240p)\/video\.m3u8.*$/, '');
}

function isVideoDownloading(video) {
    const vid = extractVideoIdFromUrl(video.pageUrl || video.url);
    for (const task of downloadTasks.values()) {
        if (!task.url) continue;
        if (extractVideoIdFromUrl(task.url) === vid) return true;
        if (task.url === video.url) return true;
        if (video.qualities && video.qualities.some(function (q) { return q.url === task.url; })) {
            return true;
        }
    }
    return false;
}

function mergeServerTasksFromApi(tasks) {
    if (!tasks || !tasks.length) return false;
    let changed = false;
    const apiIds = new Set(tasks.map(function (t) { return t.task_id; }));
    for (const task of tasks) {
        const prev = downloadTasks.get(task.task_id);
        if (!prev || prev.progress !== task.progress || prev.status !== task.status) {
            changed = true;
        }
        downloadTasks.set(task.task_id, task);
    }
    for (const [tid] of downloadTasks) {
        if (!apiIds.has(tid)) {
            downloadTasks.delete(tid);
            changed = true;
        }
    }
    return changed;
}

async function resolvePageUrlForDownload(video) {
  if (video.pageUrl) return normalizeUrl(video.pageUrl);
  if (video.tabId != null && video.tabId >= 0) {
    try {
      const tab = await chrome.tabs.get(video.tabId);
      if (tab && tab.url && /^https?:/i.test(tab.url)) return normalizeUrl(tab.url);
    } catch (e) {}
  }
  if (video.isYouTube && video.url) return normalizeUrl(video.url);
  if (video.isYtDlp && video.url) return normalizeUrl(video.url);
  return '';
}

function buildCookieHeaderForDownload(pageUrl) {
  if (!pageUrl || !/^https?:\/\//i.test(pageUrl)) return Promise.resolve('');
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(
      { type: 'GET_COOKIE_HEADER_FOR_URL', url: pageUrl },
      (res) => {
        if (chrome.runtime.lastError) {
          resolve('');
          return;
        }
        resolve((res && res.cookieHeader) ? res.cookieHeader : '');
      }
    );
  });
}

/** API 呼び出しは background 経由（sidepanel 直 fetch が失敗する環境対策） */
function bgServerFetch(url, options, responseType) {
    return new Promise(function (resolve, reject) {
        chrome.runtime.sendMessage(
            {
                type: 'SERVER_FETCH',
                url: url,
                authToken: authToken || undefined,
                options: {
                    method: (options && options.method) || 'GET',
                    headers: (options && options.headers) || undefined,
                    body: (options && options.body) || undefined,
                },
                timeoutMs: FETCH_TIMEOUT_MS,
                responseType: responseType || 'text',
            },
            function (resp) {
                if (chrome.runtime.lastError) {
                    reject(new TypeError(chrome.runtime.lastError.message));
                    return;
                }
                if (!resp) {
                    reject(new TypeError('Failed to fetch'));
                    return;
                }
                if (resp.error) {
                    reject(new TypeError(resp.error));
                    return;
                }
                resolve(resp);
            }
        );
    });
}

function respToFetchLike(resp) {
    const ok = resp.status >= 200 && resp.status < 300;
    return {
        ok: ok,
        status: resp.status,
        json: function () {
            return Promise.resolve(JSON.parse(resp.body || '{}'));
        },
        blob: function () {
            const bin = atob(resp.bodyBase64 || '');
            const bytes = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
            return Promise.resolve(new Blob([bytes], { type: resp.contentType || 'image/jpeg' }));
        },
        text: function () {
            return Promise.resolve(resp.body || '');
        },
    };
}

/** サーバー Offline 時は CDN 直リンクを試す（プロキシ不要なホスト向け） */
function applyDirectThumbnails() {
    const container = document.getElementById('videoList');
    if (!container) return;
    container.querySelectorAll('.videos-section img[data-thumb-url]').forEach(function (img) {
        if (!img.classList.contains('thumb-direct') && !img.classList.contains('thumb-proxied')) return;
        if (img.src && (img.src.startsWith('blob:') || img.src.startsWith('http'))) return;
        const u = img.getAttribute('data-thumb-url');
        if (u && img.classList.contains('thumb-direct')) img.src = u;
    });
}

async function proxyOneThumbnail(img, base) {
    if (!serverReachable) return;
    const thumbUrl = img.getAttribute('data-thumb-url');
    const card = img.closest('.video-card');
    if (!card || !thumbUrl) return;
    const key = decodeURIComponent(card.dataset.key);
    const video = detectedVideos.get(key);
    if (!video) return;

    const pageUrl = await resolvePageUrlForDownload(video);
    const cookie = await buildCookieHeaderForDownload(pageUrl);
    const res = await authFetch(base + '/proxy-image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            url: thumbUrl,
            cookie: cookie || undefined,
            referer: pageUrl || undefined
        })
    });
    if (!res.ok) {
        if (thumbUrl) img.src = thumbUrl;
        return;
    }
    const blob = await res.blob();
    const prev = img.src && img.src.startsWith('blob:') ? img.src : null;
    img.src = URL.createObjectURL(blob);
    if (prev) URL.revokeObjectURL(prev);
}

/** CDN が <img> 直リンクを弾くため、サーバーが Cookie/Referer 付きで取得（サーバー Offline 時は直リンク） */
async function loadProxiedThumbnails() {
    const container = document.getElementById('videoList');
    if (!container) return;

    if (!serverReachable) {
        applyDirectThumbnails();
        if (!thumbProxyWarnedOffline) {
            thumbProxyWarnedOffline = true;
            console.log('[THUMB] Server offline — using direct thumbnail URLs');
        }
        return;
    }
    thumbProxyWarnedOffline = false;

    if (thumbProxyInFlight) {
        thumbProxyQueued = true;
        return;
    }
    thumbProxyInFlight = true;

    const base = serverUrl.replace(/\/$/, '');
    const imgs = Array.from(container.querySelectorAll('.videos-section img.thumb-proxied[data-thumb-url]'))
        .filter(function (img) { return !(img.src && img.src.startsWith('blob:')); });

    try {
        for (let i = 0; i < imgs.length; i += THUMB_PROXY_CONCURRENCY) {
            const batch = imgs.slice(i, i + THUMB_PROXY_CONCURRENCY);
            await Promise.all(batch.map(async function (img) {
                try {
                    await proxyOneThumbnail(img, base);
                } catch (e) {
                    const fallback = img.getAttribute('data-thumb-url');
                    if (fallback) img.src = fallback;
                }
            }));
        }
    } finally {
        thumbProxyInFlight = false;
        if (thumbProxyQueued) {
            thumbProxyQueued = false;
            loadProxiedThumbnails().catch(function () {});
        }
    }
}

/** Fetch wrapper: Bearer + background proxy; re-probes /health if flag is stale. */
function authFetch(url, options) {
    options = options || {};
    const isHealth = String(url).indexOf('/health') !== -1;
    if (!serverReachable && !isHealth) {
        return checkServer().then(function (ok) {
            if (!ok) return Promise.reject(new TypeError('Server offline'));
            return authFetch(url, options);
        });
    }
    options.headers = options.headers || {};
    if (typeof options.headers === 'object' && !(options.headers instanceof Headers)) {
        if (authToken) {
            options.headers['Authorization'] = 'Bearer ' + authToken;
        }
    }
    const isBlob = String(url).indexOf('/proxy-image') !== -1;
    return bgServerFetch(url, options, isBlob ? 'blob' : 'text').then(function (resp) {
        return respToFetchLike(resp);
    });
}

// === SSE Connection for real-time task updates ===
function connectSSE() {
    if (!serverReachable) {
        if (sseConnection) {
            sseConnection.close();
            sseConnection = null;
        }
        return;
    }
    if (sseConnection) {
        sseConnection.close();
        sseConnection = null;
    }
    const sseUrl = serverUrl + '/tasks/events' + (authToken ? '?token=' + encodeURIComponent(authToken) : '');
    const es = new EventSource(sseUrl);
    sseConnection = es;

    es.onopen = function () {
        sseRetryMs = 3000;
    };

    es.addEventListener('task-update', (e) => {
        try {
            const task = JSON.parse(e.data);
            const existing = downloadTasks.get(task.task_id);
            const isNew = !existing;
            const statusChanged = existing && existing.status !== task.status;
            downloadTasks.set(task.task_id, task);

            if (task.status === 'completed' && task.filename && (isNew || statusChanged)) {
                recordCompletedTask(task);
            }
            updateTasksOnly();
        } catch (err) {
            console.warn('[MATRIX-M] SSE task-update parse error:', err);
        }
    });

    es.addEventListener('task-remove', (e) => {
        try {
            const data = JSON.parse(e.data);
            downloadTasks.delete(data.task_id);
            updateTasksOnly();
        } catch (err) {}
    });

    es.onerror = () => {
        es.close();
        sseConnection = null;
        const delay = sseRetryMs;
        sseRetryMs = Math.min(sseRetryMs * 2, SSE_RETRY_MAX_MS);
        setTimeout(connectSSE, delay);
    };
}

document.addEventListener('DOMContentLoaded', () => {
    console.log('[MATRIX-M] DOMContentLoaded fired');
    init();
});

async function init() {
    try {
        const result = await chrome.storage.local.get(['serverUrl', 'savedList', 'authToken']);
        if (result.serverUrl) serverUrl = normalizeServerUrl(result.serverUrl);
        else serverUrl = normalizeServerUrl(serverUrl);
        document.getElementById('serverUrl').value = serverUrl;
        if (result.savedList) savedList = result.savedList;
        if (result.authToken) authToken = result.authToken;
    } catch (e) {}

    document.getElementById('startServerBtn').onclick = async () => {
        const base = normalizeServerUrl(serverUrl);
        try {
            const resp = await bgServerFetch(base + '/health', {}, 'text');
            if (resp.ok) {
                alert('サーバーは既に起動しています。\n' + base);
                return;
            }
        } catch (_) {}

        chrome.tabs.create({ url: MATRIX_NEO_SERVER_LAUNCH_URL, active: false }, () => {
            if (chrome.runtime.lastError) {
                alert(
                    'サーバー起動用リンクを開けませんでした: ' + chrome.runtime.lastError.message +
                    '\n\nプロジェクト直下の install-server-protocol.bat を1回実行してください。'
                );
                return;
            }
            (async function waitForServer() {
                for (let i = 0; i < 15; i++) {
                    await new Promise(function (r) { setTimeout(r, 1000); });
                    try {
                        const resp = await bgServerFetch(base + '/health', {}, 'text');
                        if (resp.ok) {
                            await checkServer();
                            return;
                        }
                    } catch (_) {}
                }
                alert(
                    'サーバーがまだ応答しません。\n\n' +
                    'フォルダを移した場合: install-server-protocol.bat を実行してからもう一度押してください。\n' +
                    '手動: start_server_terminal.bat をダブルクリック'
                );
            })();
        });
    };

    document.getElementById('settingsBtn').onclick = () => {
        document.getElementById('settingsModal').classList.add('show');
        updateSavedListDisplay();
    };

    document.getElementById('vpnStatus').onclick = () => {
        document.getElementById('vpnModal').classList.add('show');
        loadVpnDetails();
    };

    document.getElementById('closeVpnModal').onclick = () => {
        document.getElementById('vpnModal').classList.remove('show');
    };

    document.getElementById('vpnModal').onclick = (e) => {
        if (e.target.id === 'vpnModal') {
            document.getElementById('vpnModal').classList.remove('show');
        }
    };

    document.getElementById('vpnRefresh').onclick = () => {
        loadVpnDetails();
    };

    document.getElementById('closeModal').onclick = () => {
        document.getElementById('settingsModal').classList.remove('show');
    };

    document.getElementById('settingsModal').onclick = (e) => {
        if (e.target.id === 'settingsModal') {
            document.getElementById('settingsModal').classList.remove('show');
        }
    };

    document.getElementById('saveSettings').onclick = async () => {
        serverUrl = normalizeServerUrl(document.getElementById('serverUrl').value);
        document.getElementById('serverUrl').value = serverUrl;
        const tokenInput = document.getElementById('authToken');
        if (tokenInput) authToken = tokenInput.value.trim();
        await chrome.storage.local.set({ serverUrl, authToken });
        document.getElementById('settingsModal').classList.remove('show');
        serverWasReachable = false;
        checkServer().then(function (ok) {
            if (ok) connectSSE();
        });
    };

    document.getElementById('testConnection').onclick = async function () {
        try {
            const resp = await bgServerFetch(normalizeServerUrl(serverUrl) + '/health', {}, 'text');
            alert(resp.status === 200 ? 'OK! Server reachable.' : 'HTTP ' + resp.status);
        } catch (e) {
            alert('Error: ' + e.message + '\n\nServer URL: ' + serverUrl);
        }
    };

    document.getElementById('importSavedList').onclick = importFromFolder;
    document.getElementById('exportSavedList').onclick = exportSavedList;
    document.getElementById('clearSavedList').onclick = clearSavedList;


    chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
        if (msg.type === 'VIDEO_DETECTED') loadVideos();
        if (msg.type === 'QUEUE_UPDATED') {
            loadQueuedVideos();
            showQueueNotification(msg.data);
        }
        if (msg.type === 'QUEUE_FAILED') {
            alert('Queue failed: ' + (msg.data?.error || 'unknown'));
            loadQueuedVideos();
        }
        sendResponse({ok: true});
        return true;
    });

    await checkServer();
    if (serverReachable) {
        await loadServerTasks();
        connectSSE();
    }
    await loadVideos();
    await loadQueuedVideos();
    checkVpnStatus();
    // Fallback: poll tasks slowly (SSE is primary)
    setInterval(loadServerTasks, 30000);
    setInterval(loadVideos, 5000);
    setInterval(checkServer, 10000);
    setInterval(checkVpnStatus, 30000);
}

async function checkServer() {
    const el = document.getElementById('serverStatus');
    const base = (serverUrl || '').replace(/\/$/, '');
    if (!base) {
        serverReachable = false;
        if (el) {
            el.textContent = 'Offline';
            el.className = 'status offline';
        }
        return false;
    }
    try {
        const resp = await bgServerFetch(base + '/health', {}, 'text');
        serverReachable = resp.status === 200;
        if (el) {
            el.textContent = serverReachable ? 'Online' : 'Error';
            el.className = 'status ' + (serverReachable ? 'online' : 'offline');
        }
    } catch (e) {
        serverReachable = false;
        if (el) {
            el.textContent = 'Offline';
            el.className = 'status offline';
        }
    }

    if (serverReachable && !serverWasReachable) {
        connectSSE();
        loadServerTasks().catch(function () {});
    } else if (!serverReachable && serverWasReachable) {
        if (sseConnection) {
            sseConnection.close();
            sseConnection = null;
        }
    }
    serverWasReachable = serverReachable;
    return serverReachable;
}


async function checkVpnStatus() {
    const el = document.getElementById('vpnStatus');
    if (!el) return;
    if (!serverReachable) {
        el.textContent = 'VPN: Offline';
        el.className = 'vpn-status error';
        return;
    }

    try {
        const res = await authFetch(serverUrl + '/vpn-status');
        const data = await res.json();
        
        if (data.success) {
            const flag = getCountryFlag(data.country_code);
            const shortIp = data.ip.split('.').slice(0, 2).join('.') + '.*.*';
            
            if (data.warning) {
                // Japan IP without VPN detected - WARNING
                el.innerHTML = '<span class="vpn-icon">⚠️</span> EXPOSED: ' + flag + ' ' + data.country;
                el.className = 'vpn-status exposed';
            } else if (data.is_vpn) {
                // VPN detected
                el.innerHTML = '<span class="vpn-icon">🛡️</span> VPN: ' + flag + ' ' + data.country;
                el.className = 'vpn-status protected';
            } else {
                // Foreign IP (likely VPN)
                el.innerHTML = '<span class="vpn-icon">🛡️</span> ' + flag + ' ' + data.country + ' (' + shortIp + ')';
                el.className = 'vpn-status protected';
            }
        } else {
            el.textContent = 'VPN: Check Failed';
            el.className = 'vpn-status error';
        }
    } catch (e) {
        el.textContent = 'VPN: Offline';
        el.className = 'vpn-status error';
    }
}

// getCountryFlag is in utils.js


// Queue functions
async function loadQueuedVideos() {
    try {
        const result = await chrome.storage.local.get(['matrixQueue']);
        const queue = result.matrixQueue || [];

        if (queue.length === 0) return;

        let added = false;
        
        // Add queued videos to detected videos
        queue.forEach(item => {
            const key = 'queue_' + item.id;
            // Check if already exists (by key or URL)
            let exists = detectedVideos.has(key);
            if (!exists) {
                for (const [k, v] of detectedVideos) {
                    if (v.url === item.url || v.title === item.title) {
                        exists = true;
                        break;
                    }
                }
            }
            
            if (!exists) {
                detectedVideos.set(key, {
                    id: item.id,
                    url: item.url,
                    title: item.title,
                    thumbnail: item.thumbnail,
                    type: 'Queued',
                    format: 'MP4',
                    qualities: [{ label: 'Original', url: item.url }],
                    isQueued: true,
                    timestamp: Date.now()
                });
                added = true;
            }
        });

        if (added) renderVideosOnly();
    } catch (e) {
        console.error('[MATRIX-M] Error loading queue:', e);
    }
}

function showQueueNotification(data) {
    // Show a brief notification in the sidepanel
    const container = document.getElementById('videoList');
    const notification = document.createElement('div');
    notification.style.cssText = `
        position: fixed;
        top: 60px;
        left: 50%;
        transform: translateX(-50%);
        background: linear-gradient(135deg, #00ff88 0%, #00d4ff 100%);
        color: #0a1628;
        padding: 10px 20px;
        border-radius: 6px;
        font-size: 13px;
        font-weight: 600;
        z-index: 9999;
        box-shadow: 0 0 20px rgba(0, 255, 136, 0.5);
    `;
    notification.textContent = '+ ' + (data?.title?.substring(0, 25) || 'Video') + '...';
    document.body.appendChild(notification);
    
    setTimeout(() => notification.remove(), 2000);
}

function removeCompletedFromQueue(taskOrFilename, url) {
    const task = typeof taskOrFilename === 'object' ? taskOrFilename : null;
    const filename = task ? task.filename : taskOrFilename;
    const taskId = task && task.task_id;
    console.log('[MATRIX-M] removeCompletedFromQueue called:', { filename, url, taskId });

    const filenameBase = (filename || '').toLowerCase().replace('.mp4', '');
    let removed = false;

    for (const [key, video] of detectedVideos) {
        if (taskId && video.lastTaskId === taskId) {
            detectedVideos.delete(key);
            removed = true;
            break;
        }
        const videoTitle = (video.title || '').toLowerCase();
        if (videoTitle.includes(filenameBase) ||
            filenameBase.includes(videoTitle.substring(0, 30)) ||
            video.url === url) {
            detectedVideos.delete(key);
            console.log('[MATRIX-M] Removed from local detectedVideos:', video.title);
            removed = true;
            break;
        }
    }

    chrome.runtime.sendMessage({ type: 'REMOVE_VIDEO_BY_URL', url: url }).catch(() => {});

    chrome.storage.local.get(['matrixQueue'], (result) => {
        const queue = result.matrixQueue || [];
        const filtered = queue.filter(item => {
            const itemTitle = (item.title || '').toLowerCase();
            return !itemTitle.includes(filenameBase) && !filenameBase.includes(itemTitle.substring(0, 30));
        });
        if (filtered.length !== queue.length) {
            chrome.storage.local.set({ matrixQueue: filtered });
        }
    });

    if (removed) {
        renderVideosOnly();
    }
}

async function recordCompletedTask(task) {
    if (!task || !task.task_id) return;
    if (completedTaskHistoryIds.has(task.task_id)) return;
    completedTaskHistoryIds.add(task.task_id);
    if (task.filename) {
        await addToSavedList(task.filename);
    }
    await addToHistory(task);
    updateSavedListDisplay();
    removeCompletedFromQueue(task, task.url);
}

async function clearQueue() {
    await chrome.storage.local.set({ matrixQueue: [] });
    // Remove queued items from detectedVideos
    for (const [key, video] of detectedVideos) {
        if (video.isQueued) {
            detectedVideos.delete(key);
        }
    }
    renderVideosOnly();
}

// Load queue on init
async function initQueue() {
    await loadQueuedVideos();
}

async function loadVpnDetails() {
    const statusLarge = document.getElementById('vpnStatusLarge');
    const ipEl = document.getElementById('vpnIp');
    const countryEl = document.getElementById('vpnCountry');
    const cityEl = document.getElementById('vpnCity');
    const orgEl = document.getElementById('vpnOrg');
    const typeEl = document.getElementById('vpnType');
    const warningBox = document.getElementById('vpnWarningBox');
    
    // Reset to checking state
    statusLarge.className = 'vpn-status-large checking';
    statusLarge.innerHTML = '<span class="vpn-icon-large">🔄</span><span class="vpn-label">Checking...</span>';
    ipEl.textContent = '---';
    countryEl.textContent = '---';
    cityEl.textContent = '---';
    orgEl.textContent = '---';
    typeEl.textContent = '---';
    warningBox.style.display = 'none';
    
    try {
        const res = await authFetch(serverUrl + '/vpn-status');
        const data = await res.json();
        
        if (data.success) {
            const flag = getCountryFlag(data.country_code);
            
            // Update status
            if (data.warning) {
                statusLarge.className = 'vpn-status-large exposed';
                statusLarge.innerHTML = '<span class="vpn-icon-large">⚠️</span><span class="vpn-label">EXPOSED</span>';
                warningBox.style.display = 'flex';
            } else {
                statusLarge.className = 'vpn-status-large protected';
                statusLarge.innerHTML = '<span class="vpn-icon-large">🛡️</span><span class="vpn-label">PROTECTED</span>';
                warningBox.style.display = 'none';
            }
            
            // Update details
            ipEl.textContent = data.ip || 'Unknown';
            countryEl.textContent = flag + ' ' + (data.country || 'Unknown');
            cityEl.textContent = data.city || 'Unknown';
            orgEl.textContent = data.org || 'Unknown';
            
            // Determine connection type
            let connType = 'Direct Connection';
            if (data.is_vpn) {
                connType = '🛡️ VPN Protected';
            } else if (!data.is_home_country) {
                connType = '🌐 Foreign IP (Likely VPN)';
            } else {
                connType = '⚠️ Direct (No VPN)';
            }
            typeEl.textContent = connType;
            
        } else {
            statusLarge.className = 'vpn-status-large checking';
            statusLarge.innerHTML = '<span class="vpn-icon-large">❌</span><span class="vpn-label">ERROR</span>';
            ipEl.textContent = 'Failed to check';
            typeEl.textContent = data.error || 'Unknown error';
        }
    } catch (e) {
        statusLarge.className = 'vpn-status-large checking';
        statusLarge.innerHTML = '<span class="vpn-icon-large">❌</span><span class="vpn-label">OFFLINE</span>';
        ipEl.textContent = 'Server offline';
        typeEl.textContent = e.message;
    }
}

// extractVideoIdFromFilename is in utils.js

function isAlreadySaved(title) {
    if (!title || savedList.length === 0) return false;
    const videoId = extractVideoIdFromFilename(title);
    if (!videoId) return false;
    const searchId = videoId.toLowerCase();
    return savedList.some(item => item.filename.toLowerCase().includes(searchId));
}

async function addToSavedList(filename) {
    const videoId = extractVideoIdFromFilename(filename);
    const exists = savedList.some(item => extractVideoIdFromFilename(item.filename) === videoId);
    if (!exists) {
        savedList.push({ filename: filename, date: new Date().toISOString() });
        await chrome.storage.local.set({ savedList });
    }
}

async function scanFolderRecursively(dirHandle, videoExtensions, results) {
    for await (const entry of dirHandle.values()) {
        if (entry.kind === 'file') {
            const ext = entry.name.substring(entry.name.lastIndexOf('.')).toLowerCase();
            if (videoExtensions.includes(ext)) results.push(entry.name);
        } else if (entry.kind === 'directory') {
            await scanFolderRecursively(entry, videoExtensions, results);
        }
    }
}

async function importFromFolder() {
    const videoExtensions = ['.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg', '.3gp', '.ts', '.m2ts'];
    try {
        const dirHandle = await window.showDirectoryPicker({ mode: 'read' });
        const btn = document.getElementById('importSavedList');
        btn.textContent = 'Scanning...';
        btn.disabled = true;

        const foundFiles = [];
        await scanFolderRecursively(dirHandle, videoExtensions, foundFiles);

        let addedCount = 0;
        foundFiles.forEach(name => {
            const videoId = extractVideoIdFromFilename(name);
            const exists = savedList.some(item => extractVideoIdFromFilename(item.filename) === videoId);
            if (!exists) {
                savedList.push({ filename: name, date: new Date().toISOString() });
                addedCount++;
            }
        });

        await chrome.storage.local.set({ savedList });
        updateSavedListDisplay();
        renderVideosOnly();
        btn.textContent = 'Import from folder';
        btn.disabled = false;
        alert('Import complete: ' + addedCount + ' added, Total: ' + savedList.length);
    } catch (e) {
        document.getElementById('importSavedList').textContent = 'Import from folder';
        document.getElementById('importSavedList').disabled = false;
    }
}

function exportSavedList() {
    const blob = new Blob([JSON.stringify(savedList, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'matrix-m-saved-list.json';
    a.click();
}

async function clearSavedList() {
    if (confirm('Clear all ' + savedList.length + ' items?')) {
        savedList = [];
        await chrome.storage.local.set({ savedList });
        updateSavedListDisplay();
        renderVideosOnly();
    }
}

function updateSavedListDisplay() {
    const el = document.getElementById('savedListCount');
    if (el) el.textContent = savedList.length;
}

async function loadServerTasks() {
    if (!serverReachable) return;
    try {
        const res = await authFetch(serverUrl + '/tasks');
        if (!res.ok) return;
        const data = await res.json();
        if (!data.tasks) return;

        const processedCompleted = new Set();
        for (const task of data.tasks) {
            if (task.status === 'completed' && task.filename && !processedCompleted.has(task.task_id)) {
                processedCompleted.add(task.task_id);
                if (!completedTaskHistoryIds.has(task.task_id)) {
                    await recordCompletedTask(task);
                }
            }
        }
        if (mergeServerTasksFromApi(data.tasks)) {
            updateTasksOnly();
        }
    } catch (e) {
        if (serverReachable) {
            console.warn('[MATRIX-M] loadServerTasks:', e);
        }
    }
}

async function loadVideos() {
    try {
        const response = await chrome.runtime.sendMessage({ type: 'GET_VIDEOS' });
        if (!response || !response.videos) return;

        let changed = false;
        for (const v of response.videos) {
            const key = videoListKey(v);
            const existing = detectedVideos.get(key);
            if (!existing || (v.qualities?.length || 0) > (existing.qualities?.length || 0)) {
                detectedVideos.set(key, v);
                changed = true;
            } else if (v.durationSec != null && (existing.durationSec == null || existing.durationSec <= 0)) {
                existing.durationSec = v.durationSec;
                detectedVideos.set(key, existing);
                changed = true;
            }
        }
        if (changed) {
            console.log('[MATRIX-M] loadVideos merged:', response.videos.length);
            renderVideosOnly();
        }
    } catch (e) {
        console.warn('[MATRIX-M] loadVideos:', e);
    }
}

function updateTasksOnly() {
    const container = document.getElementById('videoList');
    let tasksContainer = container.querySelector('.tasks-section');
    if (!tasksContainer) {
        tasksContainer = document.createElement('div');
        tasksContainer.className = 'tasks-section';
        container.insertBefore(tasksContainer, container.firstChild);
    }

    let html = '';
    let activeCount = 0, stoppedCount = 0, completedCount = 0;
    
    downloadTasks.forEach((task, taskId) => {
        const isStopped = task.status === 'stopped';
        const isCompleted = task.status === 'completed';
        const isError = task.status === 'error';
        const isActive = ['downloading','queued','merging','thumbnail'].includes(task.status);
        
        if (isActive) activeCount++;
        if (isStopped) stoppedCount++;
        if (isCompleted || isError) completedCount++;
        
        if (isCompleted || isError) {
            html += '<div class="video-card ' + (isCompleted ? 'completed' : 'error') + '">';
            html += '<div class="video-info"><div class="video-title">' + escapeHtml(task.filename || 'Unknown') + '</div>';
            html += '<div class="video-meta"><span class="status-badge ' + task.status + '">' + (isCompleted ? 'Completed!' : 'Error') + '</span></div></div>';
            html += '<button class="btn-close" data-action="delete" data-task-id="' + taskId + '">&times;</button>';
            html += '</div>';
        } else if (isStopped) {
            html += '<div class="video-card stopped">';
            html += '<div class="video-info"><div class="video-title">' + escapeHtml(task.filename || 'Unknown') + '</div>';
            html += '<div class="video-meta"><span class="status-badge stopped">Stopped</span></div></div>';
            html += '<div class="task-btns"><button class="btn btn-resume" data-action="resume" data-task-id="' + taskId + '">Resume</button></div>';
            html += '</div>';
        } else if (isActive) {
            html += '<div class="video-card downloading">';
            html += '<div class="video-info"><div class="video-title">' + escapeHtml(task.filename || 'Unknown') + '</div>';
            html += '<div class="video-meta"><span class="status-badge ' + task.status + '">' + (task.message || task.status) + '</span></div></div>';
            html += '<div class="progress-container"><div class="progress-bar"><div class="progress-fill" style="width:' + (task.progress||0) + '%"></div></div>';
            html += '<div class="progress-row"><span>' + (task.progress||0) + '%</span><button class="btn-stop" data-action="stop" data-task-id="' + taskId + '">Stop</button></div></div>';
            html += '</div>';
        }
    });
    
    const queuedCount = [...detectedVideos.values()].filter(function (v) { return v.isQueued; }).length;
    let header = '<div class="tasks-header"><span>DL(' + activeCount + ')</span><div>';
    if (queuedCount > 0) {
        header += '<button class="btn-sm" data-action="seq-queue">順次DL(' + queuedCount + ')</button>';
    }
    if (completedCount > 0) header += '<button class="btn-sm btn-success" data-action="clear-completed">Clear Done (' + completedCount + ')</button>';
    if (activeCount > 0) header += '<button class="btn-sm btn-danger" data-action="stop-all">Stop All</button>';
    if (stoppedCount > 0) header += '<button class="btn-sm" data-action="clear-stopped">Clear</button>';
    header += '</div></div>';
    html = header + html;

    
    tasksContainer.innerHTML = html;
    bindTaskEvents();
    updateEmptyMessage();
}

async function renderVideosOnly() {
    const container = document.getElementById('videoList');
    let videosContainer = container.querySelector('.videos-section');
    if (!videosContainer) {
        videosContainer = document.createElement('div');
        videosContainer.className = 'videos-section';
        container.appendChild(videosContainer);
    }

    let html = '';
    let dlHistory = [];
    try {
        const histResult = await chrome.storage.local.get(['downloadHistory']);
        dlHistory = histResult.downloadHistory || [];
    } catch {}
    detectedVideos.forEach((video, key) => {
        if (isVideoDownloading(video) || pendingDownloadKeys.has(key)) return;

        const isSaved = isAlreadySaved(video.title);
        const dlDone = isDownloadedUrl(video.url, dlHistory);

        const savedQuality = selectedQualities.get(key);

        // --- 品質 <select> 構築 ---
        let options = '';
        const isResolvingQualities = (video.isYtDlp || video.isYouTube) && video._qualitiesResolved === false;

        if (video.isYtDlp || video.isYouTube) {
            const quals = video.qualities && video.qualities.length > 0
                ? video.qualities
                : [{ label: 'Best', value: 'best' }];
            quals.forEach((q, i) => {
                const val = q.value != null ? String(q.value) : (q.url || '');
                const isSelected = savedQuality ? (val === savedQuality) : (i === 0);
                options += '<option value="' + escapeHtml(val) + '"' + (isSelected ? ' selected' : '') + '>' + escapeHtml(q.label) + '</option>';
            });
        } else if (video.qualities && video.qualities.length > 0) {
            video.qualities.forEach((q, i) => {
                const isSelected = savedQuality ? (q.url === savedQuality) : (i === 0);
                options += '<option value="' + escapeHtml(q.url) + '"' + (isSelected ? ' selected' : '') + '>' + escapeHtml(q.label) + '</option>';
            });
        } else {
            options = '<option value="' + escapeHtml(video.url) + '">Original</option>';
        }

        const hasThumbnail = video.thumbnail ? ' has-thumb' : '';
        const durLabel = formatDurationLabel(video.durationSec);

        html += '<div class="video-card detected' + (isSaved ? ' saved' : '') + (dlDone ? ' is-downloaded' : '') + hasThumbnail + '" data-key="' + encodeURIComponent(key) + '">';

        if (video.thumbnail) {
            const thumbClass = /img\.supjav\.com\/images\//i.test(video.thumbnail)
                ? 'thumb-direct'
                : 'thumb-proxied';
            html += '<div class="video-thumb"><img class="' + thumbClass + '" data-thumb-url="' + escapeHtml(video.thumbnail) + '" alt=""></div>';
        }

        html += '<div class="video-info">';
        html += '<div class="video-title">' + escapeHtml(video.title || 'Unknown Video') + (dlDone ? '<span class="downloaded-badge">DL済</span>' : '') + '</div>';
        html += '<div class="video-meta">';
        html += '<span class="duration-badge" title="長さ">' + (durLabel ? escapeHtml(durLabel) : '—') + '</span>';
        if (isSaved) html += '<span class="saved-tag">Saved</span>';
        if (video.thumbnail) html += '<span class="thumb-tag">Thumb</span>';
        html += '<span>' + escapeHtml(video.type || '') + '</span><span>' + (isResolvingQualities ? 'Loading...' : ((video.qualities?.length || 1) + 'Q')) + '</span></div></div>';

        html += '<div class="video-actions">';
        html += '<select class="quality-select">' + options + '</select>';
        html += '<button class="download-btn">DL</button>';
        html += '<button class="remove-btn">X</button></div></div>';
    });

    videosContainer.innerHTML = html;
    bindVideoEvents();
    updateEmptyMessage();
    applyDirectThumbnails();
    loadProxiedThumbnails().catch(function () {});
}

function bindVideoEvents() {
    document.getElementById('videoList').querySelectorAll('.video-card.detected').forEach(card => {
        const key = decodeURIComponent(card.dataset.key);
        const video = detectedVideos.get(key);
        if (!video) return;

        const select = card.querySelector('.quality-select');
        const dlBtn = card.querySelector('.download-btn');
        const rmBtn = card.querySelector('.remove-btn');

        if (select) select.onchange = function() { selectedQualities.set(key, this.value); };

        if (dlBtn) dlBtn.onclick = function() { startDownload(video, select.value); };
        if (rmBtn) rmBtn.onclick = function() {
            detectedVideos.delete(key);
            selectedQualities.delete(key);
            chrome.runtime.sendMessage({ type: 'REMOVE_VIDEO_BY_URL', url: video.url });
            renderVideosOnly();
        };
    });
}

function bindTaskEvents() {
    document.querySelectorAll('[data-action="stop"]').forEach(btn => {
        btn.onclick = function() { stopTask(this.dataset.taskId); };
    });
    document.querySelectorAll('[data-action="resume"]').forEach(btn => {
        btn.onclick = function() { resumeTask(this.dataset.taskId); };
    });
    document.querySelectorAll('[data-action="stop-all"]').forEach(btn => {
        btn.onclick = function() { stopAllTasks(); };
    });
    document.querySelectorAll('[data-action="clear-stopped"]').forEach(btn => {
        btn.onclick = function() { clearStoppedTasks(); };
    });
    document.querySelectorAll('[data-action="clear-completed"]').forEach(btn => {
        btn.onclick = function() { clearCompletedTasks(); };
    });
    document.querySelectorAll('[data-action="delete"]').forEach(btn => {
        btn.onclick = function() { deleteTask(this.dataset.taskId); };
    });
    document.querySelectorAll('[data-action="seq-queue"]').forEach(btn => {
        btn.onclick = function() { startSequentialQueueDownloads(); };
    });
}

async function startSequentialQueueDownloads() {
    if (sequentialQueueRunning) return;
    const entries = [...detectedVideos.entries()].filter(function (pair) { return pair[1].isQueued; });
    if (entries.length === 0) return;
    if (!confirm('キュー内 ' + entries.length + ' 件を順次ダウンロードしますか？')) return;

    sequentialQueueRunning = true;
    try {
        for (const [, video] of entries) {
            let dlUrl = video.url;
            if (video.qualities && video.qualities.length > 0) {
                dlUrl = video.qualities[0].url || video.qualities[0].value || video.url;
            }
            await startDownload(video, dlUrl);
            while (true) {
                const active = [...downloadTasks.values()].some(function (t) {
                    return ['queued', 'downloading', 'merging', 'thumbnail'].includes(t.status);
                });
                if (!active && !isVideoDownloading(video)) break;
                await new Promise(function (r) { setTimeout(r, 1500); });
            }
        }
    } finally {
        sequentialQueueRunning = false;
    }
}

function updateEmptyMessage() {
    const container = document.getElementById('videoList');
    let emptyMsg = container.querySelector('.empty-message');
    if (!downloadTasks.size && !detectedVideos.size) {
        if (!emptyMsg) {
            emptyMsg = document.createElement('div');
            emptyMsg.className = 'empty-message';
            emptyMsg.textContent = 'No videos detected';
            container.appendChild(emptyMsg);
        }
    } else if (emptyMsg) {
        emptyMsg.remove();
    }
}

function removeDetectedByVideo(video) {
    for (const [k, v] of detectedVideos) {
        if (v.id === video.id || v.url === video.url) {
            detectedVideos.delete(k);
            selectedQualities.delete(k);
            pendingDownloadKeys.delete(k);
            return k;
        }
    }
    return null;
}

function isLikelyStreamUrl(u) {
    if (!u || !/^https?:\/\//i.test(u)) return false;
    if (/\.html?(\?|#|$)/i.test(u)) return false;
    if (/supjav\.com\/ja\/\d+\.html/i.test(u)) return false;
    return /\.m3u8|\.mpd|\.mp4|\.webm|\.m4v|\/hls\/|\/stream\//i.test(u);
}

async function startDownload(video, url) {
    const key = videoListKey(video);
    if (pendingDownloadKeys.has(key) || isVideoDownloading(video)) {
        return;
    }
    pendingDownloadKeys.add(key);
    renderVideosOnly();

    try {
        if (!video.isYouTube && !video.isYtDlp && !isLikelyStreamUrl(url)) {
            alert(
                'ストリームURLが検出されていません。\n' +
                '動画ページで再生ボタンを押し、サイドパネルに HLS/MP4 が表示されてから DL してください。'
            );
            return;
        }
        const histCheck = await chrome.storage.local.get(['downloadHistory']);
        const hist = histCheck.downloadHistory || [];
        if (isDownloadedUrl(video.url, hist)) {
            if (!confirm('この動画は既にDL済みです。再度ダウンロードしますか？')) {
                return;
            }
        }
        const filename = (video.title || 'video').replace(/[<>:"/\\|?*]/g, '_').substring(0, 80);
        let endpoint = serverUrl + '/download';
        let body;

        if (video.isYouTube || video.isYtDlp) {
            let qualityValue = '1080';
            if (url && video.qualities && video.qualities.length) {
                const selectedQ = video.qualities.find(function (q) {
                    return q.url === url || String(q.value) === String(url);
                });
                if (selectedQ) {
                    if (selectedQ.value === 'best') {
                        qualityValue = '4320';
                    } else if (selectedQ.value != null && selectedQ.value !== '') {
                        qualityValue = String(selectedQ.value);
                    } else if (selectedQ.label) {
                        const numMatch = selectedQ.label.match(/(\d+)/);
                        if (numMatch) qualityValue = numMatch[1];
                    }
                }
            }
            endpoint = serverUrl + '/youtube/download';
            body = {
                url: video.pageUrl || video.url,
                filename: filename,
                format_type: 'mp4',
                quality: qualityValue,
                thumbnail: true
            };
        } else {
            body = { url: url, filename: filename + '.mp4', format_type: 'mp4' };
            if (video.thumbnail) body.thumbnail_url = video.thumbnail;
            const pageUrl = await resolvePageUrlForDownload(video);
            if (pageUrl) {
                const cookieHeader = await buildCookieHeaderForDownload(pageUrl);
                if (cookieHeader) body.cookie = cookieHeader;
                body.referer = pageUrl;
            }
        }

        const res = await authFetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await res.json().catch(function () { return {}; });
        if (!res.ok) {
            throw new Error(data.detail || ('HTTP ' + res.status));
        }
        if (!data.task_id) {
            throw new Error('No task_id in response');
        }

        const startedVideo = { ...video, lastTaskId: data.task_id };
        removeDetectedByVideo(startedVideo);
        chrome.runtime.sendMessage({ type: 'REMOVE_VIDEO_BY_URL', url: video.url });
        await loadServerTasks();
        renderVideosOnly();
        if (data.deduplicated) {
            console.log('[MATRIX-M] Download already in flight:', data.task_id);
        }
    } catch (e) {
        alert('Error: ' + e.message);
        renderVideosOnly();
    } finally {
        pendingDownloadKeys.delete(key);
    }
}

// Tab switching
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.onclick = function() {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        this.classList.add('active');
        document.getElementById(this.dataset.tab + 'Tab').classList.add('active');
        if (this.dataset.tab === 'history') loadHistory();
    };
});

// History functions
let downloadHistory = [];

async function loadHistory() {
    try {
        const result = await chrome.storage.local.get(['downloadHistory']);
        downloadHistory = result.downloadHistory || [];
        renderHistory();
    } catch (e) {}
}

function renderHistory() {
    const container = document.getElementById('historyList');
    const countEl = document.getElementById('historyCount');
    
    if (!container) return;
    countEl.textContent = downloadHistory.length;
    
    if (downloadHistory.length === 0) {
        container.innerHTML = '<div class="history-empty">No download history</div>';
        return;
    }
    
    const grouped = {};
    downloadHistory.forEach((item, idx) => {
        const dateKey = new Date(item.date).toLocaleDateString('ja-JP');
        if (!grouped[dateKey]) grouped[dateKey] = [];
        grouped[dateKey].push({ ...item, originalIndex: idx });
    });
    
    const sortedDates = Object.keys(grouped).sort((a, b) => new Date(b) - new Date(a));
    
    let html = '';
    sortedDates.forEach(dateKey => {
        const items = grouped[dateKey];
        html += '<div class="history-group">';
        html += '<div class="history-date" data-action="toggle-group">';
        html += '<span class="toggle-icon">&#9660;</span> ' + dateKey + ' <span class="count">(' + items.length + ')</span>';
        html += '</div>';
        html += '<div class="history-group-items">';
        
        items.slice().reverse().forEach(item => {
            const time = new Date(item.date).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' });
            html += '<div class="history-item" data-index="' + item.originalIndex + '">';
            html += '<div class="title">' + escapeHtml(item.filename) + '</div>';
            html += '<div class="meta"><span>' + time + '</span><span>' + (item.status || 'completed') + '</span></div>';
            html += '<div class="actions">';
            html += '<button class="btn btn-sm" data-action="redownload">Re-DL</button>';
            html += '<button class="btn btn-sm btn-danger" data-action="remove-history">Del</button>';
            html += '</div></div>';
        });
        
        html += '</div></div>';
    });
    
    container.innerHTML = html;
    bindHistoryEvents();
}

function bindHistoryEvents() {
    document.querySelectorAll('[data-action="toggle-group"]').forEach(el => {
        el.onclick = function() {
            const group = this.closest('.history-group');
            const items = group.querySelector('.history-group-items');
            const icon = this.querySelector('.toggle-icon');
            
            if (items.classList.contains('collapsed')) {
                items.classList.remove('collapsed');
                icon.innerHTML = '&#9660;';
            } else {
                items.classList.add('collapsed');
                icon.innerHTML = '&#9654;';
            }
        };
    });
    
    document.querySelectorAll('.history-item [data-action="remove-history"]').forEach(btn => {
        btn.onclick = async function() {
            const index = parseInt(this.closest('.history-item').dataset.index);
            downloadHistory.splice(index, 1);
            await chrome.storage.local.set({ downloadHistory });
            renderHistory();
        };
    });
    
    document.querySelectorAll('.history-item [data-action="redownload"]').forEach(btn => {
        btn.onclick = async function() {
            const index = parseInt(this.closest('.history-item').dataset.index);
            const item = downloadHistory[index];
            if (item && item.url) {
                try {
                    const body = { url: item.url, filename: item.filename, format_type: 'mp4' };
                    if (item.thumbnail_url) body.thumbnail_url = item.thumbnail_url;
                    
                    await authFetch(serverUrl + '/download', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body)
                    });
                    
                    document.querySelector('[data-tab="download"]').click();
                    await loadServerTasks();
                } catch (e) {
                    alert('Error: ' + e.message);
                }
            }
        };
    });
}

async function addToHistory(item) {
    try {
        const result = await chrome.storage.local.get(['downloadHistory']);
        downloadHistory = result.downloadHistory || [];
        if (downloadHistory.some(function (h) {
            return (h.task_id && h.task_id === item.task_id) ||
                (h.filename === item.filename && h.url === item.url);
        })) {
            return;
        }
        downloadHistory.push({
            task_id: item.task_id,
            filename: item.filename,
            url: item.url,
            thumbnail_url: item.thumbnail_url,
            date: new Date().toISOString(),
            status: 'completed'
        });
        await chrome.storage.local.set({ downloadHistory });
    } catch (e) {}
}

document.getElementById('clearHistory').onclick = async function() {
    if (confirm('Clear all history?')) {
        downloadHistory = [];
        await chrome.storage.local.set({ downloadHistory });
        renderHistory();
    }
};

// escapeHtml is in utils.js

// Stop/Resume Functions
async function stopTask(taskId) {
    try {
        const res = await authFetch(serverUrl + '/task/' + taskId + '/stop', { method: 'POST' });
        if (res.ok) await loadServerTasks();
    } catch (e) { alert('Error: ' + e.message); }
}

async function resumeTask(taskId) {
    try {
        const res = await authFetch(serverUrl + '/task/' + taskId + '/resume', { method: 'POST' });
        if (res.ok) await loadServerTasks();
    } catch (e) { alert('Error: ' + e.message); }
}

async function stopAllTasks() {
    if (!confirm('Stop all downloads?')) return;
    try {
        await authFetch(serverUrl + '/tasks/stop-all', { method: 'POST' });
        await loadServerTasks();
    } catch (e) { alert('Error: ' + e.message); }
}

async function clearStoppedTasks() {
    try {
        await authFetch(serverUrl + '/tasks/clear-stopped', { method: 'DELETE' });
        await loadServerTasks();
    } catch (e) { alert('Error: ' + e.message); }
}

async function clearFinishedTasks() {
    try {
        await authFetch(serverUrl + '/tasks/clear-finished', { method: 'DELETE' });
        await loadServerTasks();
    } catch (e) { alert('Error: ' + e.message); }
}

async function clearCompletedTasks() {
    await clearFinishedTasks();
    const completedIds = [];
    downloadTasks.forEach((task, taskId) => {
        if (task.status === 'completed' || task.status === 'error') {
            completedIds.push(taskId);
        }
    });
    for (const taskId of completedIds) {
        downloadTasks.delete(taskId);
    }
    updateTasksOnly();
    console.log('[MATRIX-M] Cleared ' + completedIds.length + ' finished tasks');
}

async function deleteTask(taskId) {
    try {
        await authFetch(serverUrl + '/task/' + taskId, { method: 'DELETE' });
        downloadTasks.delete(taskId);
        updateTasksOnly();
    } catch (e) { alert('Error: ' + e.message); }
}
// Site Structure Analyzer
document.getElementById('analyzeBtn').onclick = async function() {
    const btn = this;
    const output = document.getElementById('analysisOutput');
    
    btn.disabled = true;
    btn.textContent = 'Analyzing...';
    output.value = 'Scanning page structure...\n';
    
    try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        
        const results = await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            func: () => {
                let result = '';
                result += '===== VIDEO SITE ANALYZER =====\n';
                result += 'URL: ' + location.href + '\n';
                result += 'Time: ' + new Date().toLocaleString() + '\n\n';

                // 1. Meta Data
                result += '--- META DATA ---\n';
                result += 'og:title: ' + (document.querySelector('meta[property="og:title"]')?.content || 'N/A') + '\n';
                result += 'og:image: ' + (document.querySelector('meta[property="og:image"]')?.content || 'N/A') + '\n';
                result += 'og:video: ' + (document.querySelector('meta[property="og:video"]')?.content || 'N/A') + '\n';
                result += 'twitter:image: ' + (document.querySelector('meta[name="twitter:image"]')?.content || 'N/A') + '\n';
                const desc = document.querySelector('meta[name="description"]')?.content;
                result += 'description: ' + (desc ? desc.substring(0, 100) + '...' : 'N/A') + '\n\n';

                // 2. Video Elements
                result += '--- VIDEO ELEMENTS ---\n';
                const videos = document.querySelectorAll('video');
                result += 'Video tags found: ' + videos.length + '\n';
                videos.forEach((v, i) => {
                    result += 'Video ' + i + ':\n';
                    result += '  src: ' + (v.src ? v.src.substring(0, 80) : 'N/A') + '\n';
                    result += '  poster: ' + (v.poster ? v.poster.substring(0, 80) : 'N/A') + '\n';
                    const sources = [...v.querySelectorAll('source')].map(s => s.src?.substring(0, 60));
                    if (sources.length) result += '  sources: ' + sources.join(', ') + '\n';
                });
                result += '\n';

                // 3. Title Candidates
                result += '--- TITLE CANDIDATES ---\n';
                result += 'document.title: ' + document.title + '\n';
                result += 'h1: ' + (document.querySelector('h1')?.textContent?.trim()?.substring(0, 80) || 'N/A') + '\n';
                result += 'h2: ' + (document.querySelector('h2')?.textContent?.trim()?.substring(0, 80) || 'N/A') + '\n';
                result += '.title: ' + (document.querySelector('.title, [class*="title"]')?.textContent?.trim()?.substring(0, 80) || 'N/A') + '\n\n';

                // 4. Thumbnail Candidates
                result += '--- THUMBNAIL CANDIDATES ---\n';
                const imgs = [...document.querySelectorAll('img')].filter(img => 
                    img.src && !img.src.includes('data:') && img.width > 100
                ).slice(0, 8);
                imgs.forEach((img, i) => {
                    result += 'img ' + i + ': ' + img.src.substring(0, 70) + ' (' + img.width + 'x' + img.height + ')\n';
                });
                result += '\n';

                // 5. Channel/Uploader Candidates
                result += '--- CHANNEL/UPLOADER CANDIDATES ---\n';
                const channelSelectors = [
                    '.channel', '.channel-name', '.uploader', '.author', '.user', '.username',
                    '[class*="channel"]', '[class*="uploader"]', '[class*="author"]', '[class*="user"]',
                    'a[href*="/channel"]', 'a[href*="/user"]', 'a[href*="/@"]',
                    '.usernameWrap a', '.usernameBadgesWrapper'
                ];
                channelSelectors.forEach(sel => {
                    try {
                        const el = document.querySelector(sel);
                        if (el?.textContent?.trim()) {
                            result += sel + ': ' + el.textContent.trim().substring(0, 50) + '\n';
                        }
                    } catch(e) {}
                });
                result += '\n';

                // 6. JSON-LD Data
                result += '--- JSON-LD STRUCTURED DATA ---\n';
                document.querySelectorAll('script[type="application/ld+json"]').forEach((script, i) => {
                    try {
                        const data = JSON.parse(script.textContent);
                        result += 'JSON-LD ' + i + ':\n';
                        result += '  @type: ' + (data['@type'] || 'N/A') + '\n';
                        result += '  name: ' + (data.name?.substring(0, 50) || 'N/A') + '\n';
                        result += '  thumbnail: ' + (data.thumbnailUrl?.substring(0, 60) || 'N/A') + '\n';
                        result += '  author: ' + (data.author?.name || data.author || 'N/A') + '\n';
                    } catch (e) {}
                });
                result += '\n';

                // 7. Streaming URLs in page (+ script tags — supjav etc.)
                result += '--- STREAMING URLs (in page source) ---\n';
                let pageText = document.body?.innerHTML || '';
                document.querySelectorAll('script').forEach(function (s) {
                    pageText += s.textContent || '';
                });
                document.querySelectorAll('iframe[src]').forEach(function (f) {
                    pageText += ' ' + f.src + ' ';
                });
                const m3u8Match = pageText.match(/https?:\/\/[^"'\s<>]+\.m3u8[^"'\s<>]*/g);
                const mpdMatch = pageText.match(/https?:\/\/[^"'\s<>]+\.mpd[^"'\s<>]*/g);
                const hlsPathMatch = pageText.match(/https?:\/\/[^"'\s<>]*\/hls\/[^"'\s<>]*/g);
                if (m3u8Match) {
                    result += 'm3u8 URLs (' + m3u8Match.length + '):\n';
                    [...new Set(m3u8Match)].slice(0, 5).forEach(url => {
                        result += '  ' + url.substring(0, 80) + '\n';
                    });
                } else if (hlsPathMatch && hlsPathMatch.length) {
                    result += 'hls path URLs (' + hlsPathMatch.length + '):\n';
                    [...new Set(hlsPathMatch)].slice(0, 5).forEach(function (u) {
                        result += '  ' + u.substring(0, 80) + '\n';
                    });
                } else {
                    result += 'm3u8 URLs: None found (再生後に再分析してください)\n';
                }
                if (mpdMatch) {
                    result += 'mpd URLs (' + mpdMatch.length + '):\n';
                    [...new Set(mpdMatch)].slice(0, 5).forEach(url => {
                        result += '  ' + url.substring(0, 80) + '\n';
                    });
                } else {
                    result += 'mpd URLs: None found\n';
                }
                result += '\n';

                // 8. Related Videos Structure
                result += '--- RELATED/LIST STRUCTURE ---\n';
                const listSelectors = [
                    '.video-list', '.related', '.recommend', '[class*="related"]', '[class*="recommend"]',
                    '.pcVideoListItem', '.videoBox', '.video-item', '.thumb-block',
                    'ul li a[href*="video"]', 'ul li a[href*="watch"]'
                ];
                listSelectors.forEach(sel => {
                    try {
                        const els = document.querySelectorAll(sel);
                        if (els.length > 0) {
                            result += sel + ': ' + els.length + ' items\n';
                        }
                    } catch(e) {}
                });
                result += '\n';

                // 9. Useful Selectors Summary
                result += '--- RECOMMENDED SELECTORS ---\n';
                const ogImg = document.querySelector('meta[property="og:image"]')?.content;
                const twitterImg = document.querySelector('meta[name="twitter:image"]')?.content;
                const videoPoster = document.querySelector('video')?.poster;
                
                result += 'Thumbnail: ';
                if (ogImg) result += 'meta[property="og:image"]';
                else if (twitterImg) result += 'meta[name="twitter:image"]';
                else if (videoPoster) result += 'video.poster';
                else result += 'Need manual inspection';
                result += '\n';

                result += 'Title: ';
                if (document.querySelector('h1')?.textContent?.trim()) result += 'h1';
                else if (document.querySelector('.title')?.textContent?.trim()) result += '.title';
                else result += 'document.title';
                result += '\n';

                result += '\n===== ANALYSIS COMPLETE =====\n';
                
                return result;
            }
        });

        if (results && results[0] && results[0].result) {
            output.value = results[0].result;
        } else {
            output.value = 'Error: Could not analyze page. Make sure you are on a valid webpage.';
        }
    } catch (e) {
        output.value = 'Error: ' + e.message + '\n\nMake sure:\n1. You are on a valid webpage (not chrome:// pages)\n2. The extension has permission to access the page';
    }
    
    btn.disabled = false;
    btn.textContent = 'Analyze Current Page';
};

document.getElementById('copyAnalysis').onclick = function() {
    const output = document.getElementById('analysisOutput');
    output.select();
    document.execCommand('copy');
    
    const btn = this;
    const originalText = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = originalText; }, 1500);
};