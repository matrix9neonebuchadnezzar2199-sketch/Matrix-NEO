// Runs inside player iframes (fc2stream, supremejav, etc.) via allFrames inject.
(function () {
    'use strict';

    const host = (location.hostname || '').toLowerCase();
    const href = location.href || '';

    const isPlayerHost =
        /fc2stream\.tv|supremejav\.com|surrit\.com|emturbovid\.com|javplayer\.|streamtape\.|dood/i.test(
            host
        ) || /supjav\.php/i.test(href);

    const hasVideo = !!document.querySelector('video');
    if (!isPlayerHost && !hasVideo) return;

    const sent = new Set();
    let scanTimer = null;

    const STREAM_PATTERNS = [
        /https?:\/\/[^"'\\\s<>)]+?\.m3u8(?:\?[^"'\\\s<>)]*)?/gi,
        /https?:\/\/[^"'\\\s<>)]+?\.mpd(?:\?[^"'\\\s<>)]*)?/gi,
        /https?:\/\/[^"'\\\s<>)]+?\.mp4(?:\?[^"'\\\s<>)]*)?/gi,
        /https?:\/\/[^"'\\\s<>)]+?\/(?:hls|manifest|playlist)[^"'\\\s<>)]*/gi,
    ];

    function looksLikeStreamUrl(u) {
        if (!u || u.length < 16) return false;
        if (/\.(ts|key|vtt|jpg|jpeg|png|gif|css|js)(\?|$)/i.test(u)) return false;
        if (/logo|favicon|analytics|doubleclick/i.test(u)) return false;
        return /\.m3u8|\.mpd|\.mp4|\/hls\/|\/manifest/i.test(u);
    }

    function notifyUrl(url) {
        if (!looksLikeStreamUrl(url) || sent.has(url)) return;
        sent.add(url);
        chrome.runtime
            .sendMessage({
                type: 'STREAM_URL_FOUND',
                url: url,
                title: document.title || '',
                thumbnail: null,
                pageUrl: null,
            })
            .catch(function () {});
        console.log('[MATRIX-M] Frame stream:', url.substring(0, 88));
    }

    function extractFromText(text) {
        if (!text) return;
        for (let i = 0; i < STREAM_PATTERNS.length; i++) {
            const re = STREAM_PATTERNS[i];
            re.lastIndex = 0;
            let m;
            while ((m = re.exec(text)) !== null) {
                let u = m[0].replace(/\\u002F/gi, '/').replace(/\\\//g, '/');
                notifyUrl(u);
            }
        }
    }

    function collectAndScan() {
        let blob = '';
        try {
            blob += document.documentElement.innerHTML || '';
        } catch (e) {}
        document.querySelectorAll('script').forEach(function (s) {
            blob += s.textContent || '';
        });
        document.querySelectorAll('video').forEach(function (v) {
            const s = v.currentSrc || v.src || '';
            if (s && !s.startsWith('blob:')) notifyUrl(s);
        });
        extractFromText(blob);
    }

    function scheduleScan() {
        if (scanTimer) clearTimeout(scanTimer);
        scanTimer = setTimeout(collectAndScan, 300);
    }

    function hookNetwork() {
        const check = function (url) {
            if (typeof url === 'string') notifyUrl(url);
            else if (url && typeof url.url === 'string') notifyUrl(url.url);
        };

        const origOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function (method, url) {
            check(url);
            return origOpen.apply(this, arguments);
        };

        if (typeof window.fetch === 'function') {
            const origFetch = window.fetch;
            window.fetch = function (input, init) {
                check(typeof input === 'string' ? input : input && input.url);
                return origFetch.apply(this, arguments);
            };
        }
    }

    hookNetwork();
    collectAndScan();
    [500, 2000, 5000, 12000, 20000].forEach(function (ms) {
        setTimeout(collectAndScan, ms);
    });

    if (document.body) {
        new MutationObserver(scheduleScan).observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
        });
    }
    document.addEventListener('play', scheduleScan, true);
    document.addEventListener('loadedmetadata', scheduleScan, true);

    console.log('[MATRIX-M] Frame scanner:', host || href.substring(0, 40));
})();
