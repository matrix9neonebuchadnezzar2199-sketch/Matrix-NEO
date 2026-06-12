// MATRIX-M Background Service Worker

/**
 * @param {string} pageUrl
 * @param {(res: { cookieHeader: string }) => void} callback
 */
function getCookieHeaderForUrl(pageUrl, callback) {
    if (!pageUrl || !/^https?:\/\//i.test(pageUrl)) {
        callback({ cookieHeader: '' });
        return;
    }
    try {
        chrome.cookies.getAll({ url: pageUrl }, (cookies) => {
            if (chrome.runtime.lastError) {
                console.warn('[MATRIX-M] cookies.getAll:', chrome.runtime.lastError.message);
                callback({ cookieHeader: '' });
                return;
            }
            const header = (cookies || []).map((c) => `${c.name}=${c.value}`).join('; ');
            callback({ cookieHeader: header });
        });
    } catch (e) {
        console.warn('[MATRIX-M] getCookieHeaderForUrl:', e);
        callback({ cookieHeader: '' });
    }
}

/** Return saved server URL (default http://localhost:6850). */
async function getServerUrl() {
    try {
        const result = await chrome.storage.local.get(['serverUrl']);
        const u = result.serverUrl || 'http://127.0.0.1:6850';
        return u.replace(/^http:\/\/localhost\b/i, 'http://127.0.0.1').replace(/\/$/, '');
    } catch {
        return 'http://127.0.0.1:6850';
    }
}

/** Return saved auth token (empty string = disabled). */
async function getAuthToken() {
    try {
        const result = await chrome.storage.local.get(['authToken']);
        return result.authToken || '';
    } catch {
        return '';
    }
}

/** Fetch wrapper that adds Bearer auth header when token is configured. */
async function bgAuthFetch(url, options = {}) {
    const token = await getAuthToken();
    if (token) {
        options.headers = options.headers || {};
        options.headers['Authorization'] = 'Bearer ' + token;
    }
    return fetch(url, options);
}

const HEALTH_POLL_MS = 3000;
const HEALTH_FETCH_TIMEOUT_MS = 5000;
let _lastServerOnline = false;

/** Canonical server reachability probe (runs in service worker, not sidepanel). */
async function pollServerHealth() {
    const serverUrl = await getServerUrl();
    const base = serverUrl.replace(/\/$/, '');
    let online = false;
    let detail = '';
    const controller = new AbortController();
    const timer = setTimeout(function () { controller.abort(); }, HEALTH_FETCH_TIMEOUT_MS);
    try {
        const token = await getAuthToken();
        const headers = { Accept: 'application/json' };
        if (token) headers['Authorization'] = 'Bearer ' + token;
        const res = await fetch(base + '/health', {
            method: 'GET',
            headers: headers,
            cache: 'no-store',
            signal: controller.signal,
        });
        online = res.status === 200;
        if (!online) detail = 'HTTP ' + res.status;
    } catch (e) {
        detail = e && e.message ? e.message : String(e);
    } finally {
        clearTimeout(timer);
    }

    if (online !== _lastServerOnline) {
        console.log('[MATRIX-M] Server', online ? 'online' : 'offline', base, detail || '');
        _lastServerOnline = online;
    }

    try {
        await chrome.storage.local.set({
            matrixNeoServerOnline: online,
            matrixNeoServerCheckedAt: Date.now(),
            matrixNeoServerUrl: base,
            matrixNeoServerDetail: detail,
        });
    } catch (e) {
        console.warn('[MATRIX-M] health storage:', e.message);
    }

    chrome.runtime.sendMessage({
        type: 'SERVER_REACHABILITY',
        online: online,
        serverUrl: base,
        detail: detail,
    }).catch(function () {});

    return online;
}

function startHealthPolling() {
    pollServerHealth();
    setInterval(pollServerHealth, HEALTH_POLL_MS);
}

startHealthPolling();

chrome.runtime.onMessage.addListener(function (message, _sender, sendResponse) {
    if (message.type === 'PING_SERVER_HEALTH') {
        pollServerHealth()
            .then(function (online) { sendResponse({ online: online }); })
            .catch(function (e) { sendResponse({ online: false, error: e.message }); });
        return true;
    }
    return false;
});

/**
 * yt-dlp サイトの品質をサーバー /youtube/info 経由で動的取得。
 */
async function fetchYtDlpQualities(url) {
    try {
        const base = (await getServerUrl()).replace(/\/$/, '');
        const controller = new AbortController();
        const tid = setTimeout(() => controller.abort(), 15000);
        let res;
        try {
            const token = await getAuthToken();
            const fetchOpts = { signal: controller.signal };
            if (token) fetchOpts.headers = { 'Authorization': 'Bearer ' + token };
            res = await fetch(
                base + '/youtube/info?url=' + encodeURIComponent(url),
                fetchOpts
            );
        } finally {
            clearTimeout(tid);
        }
        if (!res.ok) return null;
        const info = await res.json();

        const vq = info.video_qualities || [];
        const qualities = [{ label: 'Best', value: 'best' }];
        for (const h of vq) {
            const n = parseInt(h, 10);
            if (!n || n <= 0) continue;
            const label = n >= 2160 ? '4K' : n + 'p';
            if (!qualities.some((q) => q.value === String(n))) {
                qualities.push({ label, value: String(n) });
            }
        }

        const aq = info.audio_qualities || [];
        const audioQualities = [];
        for (const abr of aq) {
            const n = parseInt(abr, 10);
            if (!n || n <= 0) continue;
            if (!audioQualities.some((q) => q.value === String(n))) {
                audioQualities.push({ label: n + 'kbps', value: String(n) });
            }
        }

        return {
            qualities,
            audioQualities,
            duration: info.duration || null,
            title: info.title || null,
            thumbnail: info.thumbnail || null,
        };
    } catch (e) {
        console.log('[MATRIX-M] fetchYtDlpQualities failed:', e.message);
        return null;
    }
}

/**
 * Dailymotion watch-page URLs (short URL dai.ly included).
 * Extend matchYtDlpSite when adding more yt-dlp-first-party sites.
 */
function matchYtDlpSite(url) {
    if (!url) return null;
    const m1 = url.match(/^https?:\/\/(?:[a-z]+\.)?dailymotion\.com\/video\/([a-zA-Z0-9]+)/);
    if (m1) return { name: 'Dailymotion', videoId: m1[1] };
    const m2 = url.match(/^https?:\/\/dai\.ly\/([a-zA-Z0-9]+)/);
    if (m2) return { name: 'Dailymotion', videoId: m2[1] };
    return null;
}

/** Hardcoded qualities before server responds (fallback). */
const FALLBACK_VIDEO_QUALITIES = [
    { label: 'Best', value: 'best' },
    { label: '1080p', value: '1080' },
    { label: '720p', value: '720' },
    { label: '480p', value: '480' },
];
const FALLBACK_AUDIO_QUALITIES = [
    { label: '320kbps', value: '320' },
    { label: '256kbps', value: '256' },
    { label: '192kbps', value: '192' },
    { label: '128kbps', value: '128' },
];

const MAX_ANALYZE_TABS = 2;
const SESSION_VIDEOS_KEY = 'matrixNeoDetectedVideos';

/**
 * Side panel → localhost fetch is flaky on some Chrome builds; proxy via service worker.
 */
function handleServerFetch(message, sendResponse) {
    (async () => {
        const timeoutMs = message.timeoutMs || 12000;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const opts = message.options || {};
            const headers = Object.assign({}, opts.headers || {});
            if (message.authToken) {
                headers['Authorization'] = 'Bearer ' + message.authToken;
            }
            const res = await fetch(message.url, {
                method: opts.method || 'GET',
                headers: headers,
                body: opts.body,
                signal: controller.signal,
            });
            if (message.responseType === 'blob') {
                const buf = await res.arrayBuffer();
                const bytes = new Uint8Array(buf);
                let binary = '';
                const chunk = 0x8000;
                for (let i = 0; i < bytes.length; i += chunk) {
                    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                }
                sendResponse({
                    ok: res.ok,
                    status: res.status,
                    bodyBase64: btoa(binary),
                    contentType: res.headers.get('content-type') || 'application/octet-stream',
                });
            } else {
                const body = await res.text();
                sendResponse({ ok: res.ok, status: res.status, body });
            }
        } catch (e) {
            sendResponse({ ok: false, error: e.message || String(e) });
            if (String(message.url || '').indexOf('/health') !== -1) {
                console.warn('[MATRIX-M] SERVER_FETCH health failed:', message.url, e.message || e);
            }
        } finally {
            clearTimeout(timer);
        }
    })();
    return true;
}

class VideoDetector {
    constructor() {
        this.detectedVideos = new Map();
        this.processedUrls = new Map();
        this.MAX_VIDEOS = 50;
        this._analyzeRunning = 0;
        this.init();
    }

    async _acquireAnalyzeSlot() {
        while (this._analyzeRunning >= MAX_ANALYZE_TABS) {
            await new Promise((resolve) => setTimeout(resolve, 400));
        }
        this._analyzeRunning += 1;
    }

    _releaseAnalyzeSlot() {
        this._analyzeRunning = Math.max(0, this._analyzeRunning - 1);
    }

    async _persistSessionVideos() {
        try {
            await chrome.storage.session.set({
                [SESSION_VIDEOS_KEY]: Array.from(this.detectedVideos.entries()),
            });
        } catch (e) {
            console.warn('[MATRIX-M] session persist:', e.message);
        }
    }

    async _restoreSessionVideos() {
        try {
            const data = await chrome.storage.session.get([SESSION_VIDEOS_KEY]);
            const entries = data[SESSION_VIDEOS_KEY];
            if (!Array.isArray(entries)) return;
            for (const [key, video] of entries) {
                if (!this.detectedVideos.has(key)) {
                    this.detectedVideos.set(key, video);
                }
            }
            this.updateBadge();
        } catch (e) {
            console.warn('[MATRIX-M] session restore:', e.message);
        }
    }

    _removeMatrixQueueItem(itemId) {
        chrome.storage.local.get(['matrixQueue'], (result) => {
            const q = (result.matrixQueue || []).filter((i) => i.id !== itemId);
            chrome.storage.local.set({ matrixQueue: q });
        });
    }

    init() {
        this._restoreSessionVideos().then(() => {
            chrome.action.onClicked.addListener((tab) => {
                chrome.sidePanel.open({ tabId: tab.id });
            });

        chrome.webRequest.onCompleted.addListener(
            (details) => this.handleRequest(details),
            { urls: ["<all_urls>"] },
            ['responseHeaders', 'extraHeaders']
        );

        chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
            this.handleMessage(message, sender, sendResponse);
            return true;
        });

        chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
            if (changeInfo.status === 'complete' && tab.url) {
                this.checkForYouTube(tabId, tab.url, tab.title);
                this.checkForYtDlpSite(tabId, tab.url, tab.title);
                this.scheduleScanProgressiveFromDom(tabId, tab.url);
                this.scheduleSupjavFrameInject(tabId, tab.url);
            }
            if (changeInfo.status === 'loading' && changeInfo.url) {
                this.clearTabVideos(tabId);
            }
        });

        chrome.tabs.onRemoved.addListener((tabId) => {
            this.clearTabVideos(tabId);
        });

        console.log('[MATRIX-M] Background initialized');
        });
    }

    /**
     * Replace qualities on an existing entry from server /youtube/info and notify side panel.
     */
    _updateVideoQualities(key, qualities, audioQualities, extraFields) {
        const existing = this.detectedVideos.get(key);
        if (!existing) return;

        existing.qualities = qualities;
        if (audioQualities && audioQualities.length > 0) {
            existing.audioQualities = audioQualities;
        }
        if (extraFields) {
            if (extraFields.duration != null) existing.durationSec = extraFields.duration;
        }
        existing._qualitiesResolved = true;
        this.detectedVideos.set(key, existing);

        chrome.runtime.sendMessage({
            type: 'VIDEO_DETECTED',
            data: existing
        }).catch(() => {});

        console.log('[MATRIX-M] Qualities updated for', key, '→', qualities.map((q) => q.label).join(', '));
    }

    checkForYouTube(tabId, url, title) {
        const youtubePatterns = [
            /youtube\.com\/watch\?v=([a-zA-Z0-9_-]+)/,
            /youtu\.be\/([a-zA-Z0-9_-]+)/,
            /youtube\.com\/shorts\/([a-zA-Z0-9_-]+)/
        ];

        for (const pattern of youtubePatterns) {
            const match = url.match(pattern);
            if (match) {
                const videoId = match[1];
                const key = `youtube_${videoId}`;

                if (!this.detectedVideos.has(key)) {
                    const videoInfo = {
                        id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
                        url: url,
                        pageUrl: url,
                        type: 'YouTube',
                        format: 'MP4',
                        qualities: [...FALLBACK_VIDEO_QUALITIES],
                        audioQualities: [...FALLBACK_AUDIO_QUALITIES],
                        title: title ? title.replace(/^\(\d+\)\s*/, '').replace(/ - YouTube$/, '').trim() : 'YouTube Video',
                        thumbnail: `https://i.ytimg.com/vi/${videoId}/maxresdefault.jpg`,
                        isYouTube: true,
                        isYtDlp: true,
                        videoId: videoId,
                        tabId: tabId,
                        timestamp: Date.now(),
                        durationSec: null,
                        _qualitiesResolved: false,
                    };

                    this.detectedVideos.set(key, videoInfo);
                    this.cleanupOldVideos();
                    this.updateBadge();

                    chrome.runtime.sendMessage({
                        type: 'VIDEO_DETECTED',
                        data: videoInfo
                    }).catch(() => {});

                    console.log('[MATRIX-M] YouTube detected:', title, 'ID:', videoId);

                    fetchYtDlpQualities(url).then((result) => {
                        if (result && result.qualities.length > 1) {
                            this._updateVideoQualities(key, result.qualities, result.audioQualities, result);
                        }
                    });
                }
                return;
            }
        }
    }

    checkForYtDlpSite(tabId, url, title) {
        if (/youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/shorts\//i.test(url)) {
            return;
        }
        const site = matchYtDlpSite(url);
        if (!site) return;

        const videoId = site.videoId;
        const key = `dailymotion_${videoId}`;
        if (this.detectedVideos.has(key)) return;

        let cleanTitle = title || 'Dailymotion Video';
        cleanTitle = cleanTitle
            .replace(/\s*[-–|]\s*動画\s*Dailymotion\s*$/i, '')
            .replace(/\s*[-|]\s*\s*Dailymotion\s*$/i, '')
            .replace(/\s*[-|]\s*Dailymotion\s*$/i, '')
            .replace(/\s*[-|]\s*Video\s*Dailymotion\s*$/i, '')
            .trim();
        if (!cleanTitle) cleanTitle = 'Dailymotion Video';

        const videoInfo = {
            id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
            url: url,
            pageUrl: url,
            type: 'Dailymotion',
            format: 'MP4',
            qualities: [...FALLBACK_VIDEO_QUALITIES],
            title: cleanTitle,
            thumbnail: `https://www.dailymotion.com/thumbnail/video/${videoId}`,
            isYouTube: false,
            isYtDlp: true,
            videoId: videoId,
            tabId: tabId,
            timestamp: Date.now(),
            durationSec: null,
            _qualitiesResolved: false,
        };

        this.detectedVideos.set(key, videoInfo);
        this.cleanupOldVideos();
        this.updateBadge();

        chrome.runtime.sendMessage({
            type: 'VIDEO_DETECTED',
            data: videoInfo
        }).catch(() => {});

        console.log('[MATRIX-M] Dailymotion detected:', cleanTitle, 'ID:', videoId);

        fetchYtDlpQualities(url).then((result) => {
            if (result && result.qualities.length > 1) {
                this._updateVideoQualities(key, result.qualities, result.audioQualities, result);
            }
        });
    }

    getTabProcessedUrls(tabId) {
        if (!this.processedUrls.has(tabId)) {
            this.processedUrls.set(tabId, new Set());
        }
        return this.processedUrls.get(tabId);
    }

    /**
     * pathname 基準でプログレッシブか判定（クエリ付き URL でも確実にマッチ）
     */
    isProgressiveFileUrl(url) {
        try {
            const u = new URL(url);
            if (!/^https?:$/i.test(u.protocol)) return false;
            const p = u.pathname.toLowerCase();
            return p.endsWith('.mp4') || p.endsWith('.m4v') || p.endsWith('.webm');
        } catch {
            return /\.(mp4|m4v|webm)([?#&]|$)/i.test(url);
        }
    }

    /**
     * webRequest が拾えない場合（キャッシュ・タイミング等）に <video src> から検出
     */
    scheduleSupjavFrameInject(tabId, pageUrl) {
        if (!pageUrl || !/supjav\.com\/ja\/\d+\.html/i.test(pageUrl)) return;
        const delays = [0, 2000, 6000, 12000, 20000];
        for (const ms of delays) {
            setTimeout(() => this.injectStreamFrameScanner(tabId), ms);
        }
    }

    async injectStreamFrameScanner(tabId) {
        try {
            await chrome.scripting.executeScript({
                target: { tabId, allFrames: true },
                files: ['stream_frame_scanner.js'],
            });
        } catch (e) {
            console.log('[MATRIX-M] frame scanner inject:', e.message);
        }
    }

    scheduleScanProgressiveFromDom(tabId, pageUrl) {
        if (!pageUrl || !/^https?:/i.test(pageUrl)) return;
        if (/youtube\.com|youtu\.be/i.test(pageUrl)) return;
        if (matchYtDlpSite(pageUrl)) return;
        [600, 2500, 6000].forEach((ms) => {
            setTimeout(() => this.scanProgressiveVideoFromDom(tabId, pageUrl), ms);
        });
    }

    async scanProgressiveVideoFromDom(tabId, pageUrl) {
        if (!pageUrl || !/^https?:/i.test(pageUrl)) return;
        try {
            const tab = await chrome.tabs.get(tabId);
            if (!tab.url || tab.url.split('#')[0] !== pageUrl.split('#')[0]) return;
        } catch {
            return;
        }
        try {
            const results = await chrome.scripting.executeScript({
                target: { tabId },
                func: () => {
                    const out = [];
                    const push = (href) => {
                        if (href && !href.startsWith('blob:') && /^https?:/i.test(href)) {
                            out.push(href);
                        }
                    };
                    for (const v of document.querySelectorAll('video')) {
                        push(v.currentSrc || v.src || '');
                    }
                    for (const s of document.querySelectorAll('video source[src]')) {
                        try {
                            push(new URL(s.getAttribute('src') || '', document.baseURI).href);
                        } catch (e) {}
                    }
                    return [...new Set(out)];
                },
            });
            const list = (results && results[0] && results[0].result) || [];
            for (const u of list) {
                if (!this.isProgressiveFileUrl(u)) continue;
                const tabUrls = this.getTabProcessedUrls(tabId);
                if (tabUrls.has(u)) continue;
                tabUrls.add(u);
                console.log('[MATRIX-M] Progressive (DOM):', u.substring(0, 88));
                await this.processVideo(u, tabId, false);
            }
        } catch (e) {
            console.log('[MATRIX-M] DOM progressive scan:', e.message);
        }
    }

    handleRequest(details) {
        const url = details.url;
        const tabId = details.tabId;

        if (tabId < 0) return;
        if (url.startsWith('blob:')) return;

        const urlLower = url.toLowerCase();
        let isHls =
            urlLower.includes('.m3u8') ||
            /\/hls\/[^/?#]+\.m3u8/i.test(url);
        let isDash = urlLower.includes('.mpd');
        let isProg = this.isProgressiveFileUrl(url);

        if (!isHls && !isDash && !isProg) {
            const headers = details.responseHeaders || [];
            const ctHeader = headers.find((x) => x.name && x.name.toLowerCase() === 'content-type');
            const ct = ctHeader ? String(ctHeader.value || '').toLowerCase() : '';
            if (/mpegurl|x-mpegurl|vnd\.apple\.mpegurl/.test(ct)) {
                isHls = true;
            } else if (/dash\+xml/.test(ct)) {
                isDash = true;
            } else if (!isProg && /video\/(mp4|webm|mpeg)/.test(ct)) {
                isProg = true;
            }
        }

        if (!isHls && !isDash && !isProg) return;

        if (/analytics|tracking|googleads|doubleclick|adsystem|tsyndicate|googlesyndication|facebook\.com\/tr/i.test(url)) {
            return;
        }

        if (isHls || isDash) {
            if (/\.(ts|key|vtt|jpg|jpeg|png|gif|css|js)(\?|$)/i.test(url)) return;
        }

        if (isProg) {
            const st = details.statusCode;
            if (st && (st < 200 || st >= 400)) return;
            const headers = details.responseHeaders || [];
            if (headers.length > 0) {
                const getH = (n) => {
                    const h = headers.find((x) => x.name && x.name.toLowerCase() === n);
                    return h ? String(h.value || '') : '';
                };
                const ct = getH('content-type');
                const cl = parseInt(getH('content-length'), 10) || 0;
                const looksVideo =
                    /video\//i.test(ct) ||
                    /octet-stream/i.test(ct) ||
                    /mp4|webm|quicktime|mpeg/i.test(ct);
                if (cl > 0 && cl < 80 * 1024 && !looksVideo) return;
                if (cl > 0 && cl < 4096) return;
            }
        }

        const tabUrls = this.getTabProcessedUrls(tabId);
        if (tabUrls.has(url)) {
            return;
        }
        tabUrls.add(url);

        const isMaster =
            /\/playlist\.m3u8|\/master\.m3u8|\/index\.m3u8|\/manifest\.m3u8/i.test(url) ||
            (isHls && /\.m3u8/i.test(url) && !/\/\d+\.ts(\?|$)/i.test(url));
        const isQualityVariant = /\/(1080p|720p|480p|360p|240p)\/video\.m3u8/i.test(url);

        if (isHls && isQualityVariant && !isMaster) {
            const baseUrl = url.replace(/\/(1080p|720p|480p|360p|240p)\/video\.m3u8.*$/i, '');
            for (const [key, video] of this.detectedVideos) {
                if (video.url.includes(baseUrl) && video.qualities && video.qualities.length > 1) {
                    return;
                }
            }
        }

        console.log('[MATRIX-M] Video detected:', url.substring(0, 80) + '...');
        this.processVideo(url, tabId, isMaster);
    }

    async processVideo(url, tabId, isMaster, hints) {
        hints = hints || {};
        try {
            let qualities = [];
            let durationSec = null;

            const urlLower = url.toLowerCase();
            if (urlLower.includes('.m3u8')) {
                qualities = await this.fetchM3U8Qualities(url, tabId);
            } else if (urlLower.includes('.mpd')) {
                const mpdData = await this.fetchMPDData(url);
                qualities = mpdData.qualities;
            }

            let title = hints.title || 'Unknown Video';
            let thumbnail = hints.thumbnail || null;
            let isUncensored = false;

            let pageUrl = hints.pageUrl || null;
            try {
                const tab = await chrome.tabs.get(tabId);
                if (!pageUrl && tab && tab.url && /^https?:/i.test(tab.url)) {
                    pageUrl = tab.url;
                }
                if (!hints.title && tab && tab.title) {
                    title = tab.title
                        .replace(/\s*[-|]\s*MissAV.*$/i, '')
                        .replace(/\s*[-|]\s*Supjav.*$/i, '')
                        .replace(/\s*[-|]\s*[^-|]{0,15}$/g, '')
                        .trim();
                }

                const results = await chrome.scripting.executeScript({
                    target: { tabId: tabId },
                    func: () => {
                        const rawPoster = document.querySelector('video')?.poster || '';
                        const validPoster = (rawPoster && !rawPoster.startsWith('data:')) ? rawPoster : null;
                        const thumbnail = document.querySelector('meta[property="og:image"]')?.content ||
                               document.querySelector('video')?.getAttribute('data-poster') ||
                               validPoster ||
                               document.querySelector('meta[name="twitter:image"]')?.content ||
                               null;

                        const pageText = document.body?.innerText || '';
                        const hinbanMatch = pageText.match(/品番[\s:：]*([^\n]+)/);
                        const hinban = hinbanMatch ? hinbanMatch[1].trim() : '';
                        const hasUncensored = /UNCENSORED/i.test(hinban) || /UNCENSORED/i.test(document.title);

                        let durationSec = null;
                        const vel = document.querySelector('video');
                        if (
                            vel &&
                            typeof vel.duration === 'number' &&
                            Number.isFinite(vel.duration) &&
                            vel.duration > 0 &&
                            vel.duration !== Infinity
                        ) {
                            durationSec = vel.duration;
                        }

                        const channelName = document.querySelector('.video-detailed-info .usernameBadgesWrapper')?.textContent?.trim() ||
                                           document.querySelector('.usernameWrap a')?.textContent?.trim() ||
                                           null;

                        /** Pornhub: flashvars の mediaDefinitions（HLS master .m3u8 複数画質） */
                        function extractMediaDefinitionsQualities() {
                            const order = ['4K', '1080p', '720p', '480p', '360p', '240p', 'Auto'];

                            function labelFromEntry(d, videoUrl) {
                                let h = 0;
                                if (d.height != null && d.height !== '') {
                                    h = parseInt(String(d.height).replace(/\D/g, ''), 10) || 0;
                                }
                                if (!h && d.width != null && d.width !== '') {
                                    h = parseInt(String(d.width).replace(/\D/g, ''), 10) || 0;
                                }
                                if (!h && d.quality != null && d.quality !== '') {
                                    const n = parseInt(String(d.quality).replace(/\D/g, ''), 10);
                                    if (!isNaN(n)) h = n;
                                }
                                if (!h && videoUrl) {
                                    const pathMatch = videoUrl.match(/(\d{3,4})P_\d+K/i);
                                    if (pathMatch) h = parseInt(pathMatch[1], 10);
                                }
                                if (h >= 2160) return '4K';
                                if (h >= 1080) return '1080p';
                                if (h >= 720) return '720p';
                                if (h >= 480) return '480p';
                                if (h >= 360) return '360p';
                                if (h >= 240) return '240p';
                                return 'Auto';
                            }

                            function sliceBracketedArray(text, startIdx) {
                                let depth = 0;
                                let inString = false;
                                let esc = false;
                                for (let i = startIdx; i < text.length; i++) {
                                    const c = text[i];
                                    if (esc) {
                                        esc = false;
                                        continue;
                                    }
                                    if (c === '\\' && inString) {
                                        esc = true;
                                        continue;
                                    }
                                    if (c === '"' && !esc) {
                                        inString = !inString;
                                        continue;
                                    }
                                    if (!inString) {
                                        if (c === '[') depth++;
                                        else if (c === ']') {
                                            depth--;
                                            if (depth === 0) {
                                                return text.slice(startIdx, i + 1);
                                            }
                                        }
                                    }
                                }
                                return null;
                            }

                            function buildQualitiesFromArray(arr) {
                                const out = [];
                                const seen = new Set();
                                for (const d of arr) {
                                    if (!d || typeof d !== 'object') continue;
                                    const fmt = String(d.format || '').toLowerCase();
                                    if (fmt !== 'hls' && fmt !== 'mp4') continue;
                                    const videoUrl = d.videoUrl || d.url;
                                    if (!videoUrl || typeof videoUrl !== 'string') continue;
                                    if (!/\.m3u8/i.test(videoUrl)) continue;
                                    const label = labelFromEntry(d, videoUrl);
                                    if (label === 'Auto') continue;
                                    if (seen.has(label)) continue;
                                    seen.add(label);
                                    out.push({ label, url: videoUrl });
                                }
                                out.sort((a, b) => {
                                    const ia = order.indexOf(a.label);
                                    const ib = order.indexOf(b.label);
                                    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
                                });
                                return out;
                            }

                            let best = [];
                            const scriptList = document.querySelectorAll('script');
                            for (const script of scriptList) {
                                const text = script.textContent || '';
                                let searchFrom = 0;
                                while (true) {
                                    const idx = text.indexOf('mediaDefinitions', searchFrom);
                                    if (idx === -1) break;
                                    const sub = text.slice(idx);
                                    const head = sub.match(/^mediaDefinitions"?\s*[=:]\s*\[/);
                                    if (!head) {
                                        searchFrom = idx + 1;
                                        continue;
                                    }
                                    const startIdx = idx + head[0].length - 1;
                                    const jsonStr = sliceBracketedArray(text, startIdx);
                                    if (jsonStr) {
                                        try {
                                            const arr = JSON.parse(jsonStr);
                                            if (Array.isArray(arr)) {
                                                const out = buildQualitiesFromArray(arr);
                                                if (out.length > best.length) {
                                                    best = out;
                                                }
                                            }
                                        } catch (e) { console.log('[MATRIX-M] mediaDefinitions parse error:', e.message, 'jsonStr:', jsonStr.substring(0, 200)); }
                                    }
                                    searchFrom = idx + 1;
                                }
                            }
                            return best;
                        }

                        const mediaDefinitionsQualities = extractMediaDefinitionsQualities();

                        return { thumbnail, hasUncensored, channelName, mediaDefinitionsQualities, durationSec };
                    }
                });

                if (results && results[0] && results[0].result) {
                    thumbnail = results[0].result.thumbnail;
                    isUncensored = results[0].result.hasUncensored;
                    if (results[0].result.channelName) {
                        title = '[' + results[0].result.channelName + '] ' + title;
                    }
                    if (results[0].result.durationSec != null && Number.isFinite(results[0].result.durationSec)) {
                        durationSec = results[0].result.durationSec;
                    }
                    const mdq = results[0].result.mediaDefinitionsQualities;
                    if (mdq && mdq.length > 0) {
                        qualities = mdq;
                    }
                }
            } catch (e) {
                console.log('[MATRIX-M] Could not get tab info:', e.message);
            }

            if (qualities.length === 0) {
                qualities = [{ label: 'Original', url: url }];
            }

            if (isUncensored) {
                title = 'UNCENSORED ' + title;
            }

            const videoInfo = {
                id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
                url: url,
                type: urlLower.includes('.m3u8') ? 'HLS' : urlLower.includes('.mpd') ? 'DASH' : 'MP4',
                format: 'MP4',
                qualities: qualities,
                title: title || 'Unknown Video',
                thumbnail: thumbnail,
                isUncensored: isUncensored,
                tabId: tabId,
                pageUrl: pageUrl,
                timestamp: Date.now(),
                durationSec: durationSec != null && Number.isFinite(durationSec) ? durationSec : null,
                isYouTube: false,
                isYtDlp: false
            };

            const key = title !== 'Unknown Video' ? title.substring(0, 50) : url.split('?')[0];

            const existing = this.detectedVideos.get(key);
            const qualityGain = !existing || qualities.length > (existing.qualities?.length || 0);
            if (!qualityGain) {
                if (
                    videoInfo.durationSec != null &&
                    existing &&
                    (existing.durationSec == null || existing.durationSec <= 0)
                ) {
                    existing.durationSec = videoInfo.durationSec;
                    this.detectedVideos.set(key, existing);
                    this.cleanupOldVideos();
                    this.updateBadge();
                    chrome.runtime.sendMessage({
                        type: 'VIDEO_DETECTED',
                        data: existing
                    }).catch(() => {});
                }
                return;
            }

            this.detectedVideos.set(key, videoInfo);
            this.cleanupOldVideos();
            this.updateBadge();

            chrome.runtime.sendMessage({
                type: 'VIDEO_DETECTED',
                data: videoInfo
            }).catch(() => {});

            console.log('[MATRIX-M] Video added:', title,
                'Thumb:', thumbnail ? 'Yes' : 'No',
                'Uncensored:', isUncensored,
                'Duration:', videoInfo.durationSec != null ? Math.round(videoInfo.durationSec) + 's' : '—');
        } catch (error) {
            console.error('[MATRIX-M] Process error:', error);
        }
    }

    async fetchMPDData(url) {
        try {
            const response = await fetch(url);
            const text = await response.text();
            const qualities = [];

            const repMatches = text.matchAll(/<Representation[^>]*width="(\d+)"[^>]*height="(\d+)"[^>]*/g);
            for (const match of repMatches) {
                const height = parseInt(match[2]);
                const label = height >= 2160 ? '4K' : height + 'p';

                if (!qualities.find(q => q.label === label)) {
                    qualities.push({ label, url: url });
                }
            }

            return { qualities };
        } catch (error) {
            console.error('[MATRIX-M] Failed to fetch MPD:', error);
            return { qualities: [] };
        }
    }

    async fetchM3U8Qualities(url, tabId) {
        try {
            // まずService Workerから直接fetchを試みる
            let text = null;
            try {
                const response = await fetch(url);
                if (response.ok) {
                    text = await response.text();
                }
            } catch {}

            // 403等で失敗した場合、ページコンテキストでfetchする
            if (!text && tabId) {
                try {
                    const results = await chrome.scripting.executeScript({
                        target: { tabId: tabId },
                        func: (fetchUrl) => {
                            return fetch(fetchUrl).then(r => r.text()).catch(() => null);
                        },
                        args: [url]
                    });
                    if (results && results[0] && results[0].result) {
                        text = results[0].result;
                    }
                } catch (e) {
                    console.log('[MATRIX-M] Page context fetch also failed:', e.message);
                }
            }

            if (!text) {
                console.log('[MATRIX-M] Could not fetch m3u8:', url.substring(0, 60));
                return [];
            }

            const lines = text.split('\n');
            const qualities = [];

            for (let i = 0; i < lines.length; i++) {
                const line = lines[i].trim();
                if (line.startsWith('#EXT-X-STREAM-INF:')) {
                    const resMatch = line.match(/RESOLUTION=(\d+)x(\d+)/);
                    const nextLine = lines[i + 1]?.trim();

                    if (nextLine && !nextLine.startsWith('#')) {
                        const qualityUrl = nextLine.startsWith('http')
                            ? nextLine
                            : new URL(nextLine, url).href;

                        let label = 'Auto';
                        if (resMatch) {
                            const height = parseInt(resMatch[2]);
                            label = height >= 2160 ? '4K' : height + 'p';
                        }

                        qualities.push({ label, url: qualityUrl });
                    }
                }
            }

            qualities.sort((a, b) => {
                const toNum = (lbl) => {
                    if (lbl === '4K') return 2160;
                    if (lbl === 'Auto') return -1;
                    return parseInt(lbl, 10) || 0;
                };
                return toNum(b.label) - toNum(a.label);
            });

            return qualities;
        } catch (error) {
            console.error('[MATRIX-M] Failed to fetch qualities:', error);
            return [];
        }
    }


    cleanupOldVideos() {
        if (this.detectedVideos.size > this.MAX_VIDEOS) {
            const entries = Array.from(this.detectedVideos.entries());
            entries.sort((a, b) => a[1].timestamp - b[1].timestamp);
            const toRemove = entries.slice(0, entries.length - this.MAX_VIDEOS);
            toRemove.forEach(([key]) => this.detectedVideos.delete(key));
        }
        this._persistSessionVideos();
    }

    clearTabVideos(tabId) {
        for (const [key, video] of this.detectedVideos) {
            if (video.tabId === tabId) {
                this.detectedVideos.delete(key);
            }
        }
        this.processedUrls.delete(tabId);
        this.updateBadge();
        this._persistSessionVideos();
    }

    updateBadge() {
        const count = this.detectedVideos.size;
        chrome.action.setBadgeText({ text: count > 0 ? count.toString() : '' });
        chrome.action.setBadgeBackgroundColor({ color: '#4CAF50' });
    }

    handleMessage(message, sender, sendResponse) {
        switch (message.type) {
            case 'SERVER_FETCH':
                return handleServerFetch(message, sendResponse);

            case 'GET_VIDEOS':
                sendResponse({ videos: Array.from(this.detectedVideos.values()) });
                break;

            case 'GET_COOKIE_HEADER_FOR_URL': {
                getCookieHeaderForUrl(message.url || '', sendResponse);
                return true;
            }

            case 'REMOVE_VIDEO_BY_TITLE': {
                console.log('[MATRIX-M] REMOVE_VIDEO_BY_TITLE called:', message.title);
                const searchTitle = (message.title || '').toLowerCase();
                let removed = false;
                for (const [key, video] of this.detectedVideos) {
                    const videoTitle = (video.title || '').toLowerCase();
                    if (videoTitle.includes(searchTitle) || searchTitle.includes(videoTitle.substring(0, 30))) {
                        this.detectedVideos.delete(key);
                        console.log('[MATRIX-M] Removed video by title:', video.title);
                        removed = true;
                        break;
                    }
                }
                if (!removed) {
                    console.log('[MATRIX-M] No video found with title:', message.title);
                }
                this.updateBadge();
                sendResponse({ success: true, removed: removed });
                break;
            }

            case 'REMOVE_VIDEO_BY_URL': {
                console.log('[MATRIX-M] REMOVE_VIDEO_BY_URL called:', message.url?.substring(0, 50));
                let removed = false;
                for (const [key, video] of this.detectedVideos) {
                    if (video.url === message.url) {
                        this.detectedVideos.delete(key);
                        console.log('[MATRIX-M] Removed video by URL:', video.title);
                        removed = true;
                        break;
                    }
                }
                if (!removed) {
                    console.log('[MATRIX-M] No video found with URL');
                }
                this.updateBadge();
                sendResponse({ success: true, removed: removed });
                break;
            }

            case 'STREAM_URL_FOUND': {
                const tabId = sender.tab && sender.tab.id >= 0 ? sender.tab.id : null;
                if (tabId == null || !message.url) {
                    sendResponse({ success: false });
                    break;
                }
                const streamUrl = message.url;
                const tabUrls = this.getTabProcessedUrls(tabId);
                if (tabUrls.has(streamUrl)) {
                    sendResponse({ success: true, duplicate: true });
                    break;
                }
                tabUrls.add(streamUrl);
                const self = this;
                const hints = {
                    title: message.title,
                    thumbnail: message.thumbnail,
                    pageUrl: message.pageUrl,
                };
                chrome.tabs.get(tabId, (tab) => {
                    if (tab && tab.url && /supjav\.com\/ja\/\d+\.html/i.test(tab.url)) {
                        hints.pageUrl = tab.url;
                        if (!hints.title && tab.title) hints.title = tab.title;
                    }
                    console.log('[MATRIX-M] Stream from frame:', streamUrl.substring(0, 80));
                    self.processVideo(streamUrl, tabId, true, hints);
                    sendResponse({ success: true });
                });
                return true;
            }

            case 'ANALYZE_AND_QUEUE':
                console.log('[MATRIX-M] Analyzing page:', message.data?.pageUrl);
                this.analyzePageAndQueue(message.data);
                sendResponse({ success: true, status: 'analyzing' });
                break;

            case 'QUEUE_ADD':
                console.log('[MATRIX-M] Video added to queue:', message.data?.title);
                chrome.runtime.sendMessage({
                    type: 'QUEUE_UPDATED',
                    data: message.data
                }).catch(() => {});
                sendResponse({ success: true });
                break;

            case 'OPEN_SIDEPANEL':
                chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
                    if (tabs[0]) {
                        chrome.sidePanel.open({ tabId: tabs[0].id });
                    }
                });
                sendResponse({ success: true });
                break;

            case 'GET_QUEUE':
                chrome.storage.local.get(['matrixQueue'], (result) => {
                    sendResponse({ queue: result.matrixQueue || [] });
                });
                return true;

            case 'CLEAR_QUEUE':
                chrome.storage.local.set({ matrixQueue: [] });
                sendResponse({ success: true });
                break;

            case 'CLEAR_VIDEOS':
                this.detectedVideos.clear();
                this.processedUrls.clear();
                this.updateBadge();
                this._persistSessionVideos();
                sendResponse({ success: true });
                break;

            default:
                sendResponse({ error: 'Unknown message type' });
        }
    }

    async analyzePageAndQueue(item) {
        await this._acquireAnalyzeSlot();
        try {
            console.log('[MATRIX-M] Starting page analysis:', item.pageUrl);
            
            const initialSize = this.detectedVideos.size;
            const initialKeys = new Set(this.detectedVideos.keys());

            const tab = await chrome.tabs.create({
                url: item.pageUrl,
                active: false
            });

            await new Promise(resolve => {
                const listener = (tabId, changeInfo) => {
                    if (tabId === tab.id && changeInfo.status === 'complete') {
                        chrome.tabs.onUpdated.removeListener(listener);
                        resolve();
                    }
                };
                chrome.tabs.onUpdated.addListener(listener);
                setTimeout(() => {
                    chrome.tabs.onUpdated.removeListener(listener);
                    resolve();
                }, 30000);
            });

            await new Promise(resolve => setTimeout(resolve, 5000));

            let newVideo = null;
            let newKey = null;
            for (const [key, video] of this.detectedVideos) {
                if (!initialKeys.has(key) && video.tabId === tab.id) {
                    newVideo = { ...video };
                    newKey = key;
                    newVideo.isQueued = true;
                    newVideo.pageUrl = item.pageUrl;
                    newVideo.tabId = -1;
                    break;
                }
            }

            if (newVideo && newKey) {
                // Set tabId to -1 BEFORE closing tab to prevent clearTabVideos from deleting it
                newVideo.tabId = -1;
                this.detectedVideos.set(newKey, newVideo);
                console.log('[MATRIX-M] Video protected from tab cleanup:', newVideo.title);
            }

            await chrome.tabs.remove(tab.id);

            if (newVideo) {
                console.log('[MATRIX-M] Video queued successfully:', newVideo.title);
                if (item.id) {
                    this._removeMatrixQueueItem(item.id);
                }

                chrome.runtime.sendMessage({
                    type: 'VIDEO_DETECTED',
                    data: newVideo
                }).catch(() => {});

                return;
            }

            console.log('[MATRIX-M] No video detected for:', item.pageUrl);
            if (item.id) {
                this._removeMatrixQueueItem(item.id);
            }
            chrome.runtime.sendMessage({
                type: 'QUEUE_FAILED',
                data: { ...item, error: 'No video detected' }
            }).catch(() => {});

        } catch (error) {
            console.error('[MATRIX-M] Page analysis error:', error);
            if (item.id) {
                this._removeMatrixQueueItem(item.id);
            }
            chrome.runtime.sendMessage({
                type: 'QUEUE_FAILED',
                data: { ...item, error: error.message }
            }).catch(() => {});
        } finally {
            this._releaseAnalyzeSlot();
        }
    }
}

new VideoDetector();