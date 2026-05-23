// Supjav (and similar) watch-page stream discovery — m3u8 is often injected after play.
(function () {
    'use strict';

    if (!/supjav\.com/i.test(location.hostname)) return;
    if (!/\/\d+\.html/i.test(location.pathname)) return;

    const sent = new Set();
    let scanTimer = null;

    const STREAM_PATTERNS = [
        /https?:\/\/[^"'\\\s<>)]+?\.m3u8(?:\?[^"'\\\s<>)]*)?/gi,
        /https?:\/\/[^"'\\\s<>)]+?\.mpd(?:\?[^"'\\\s<>)]*)?/gi,
        /https?:\/\/[^"'\\\s<>)]+?\.mp4(?:\?[^"'\\\s<>)]*)?/gi,
        /https?:\/\/[^"'\\\s<>)]+?\/(?:hls|stream|video)[^"'\\\s<>)]*/gi,
    ];

    function decodeLoose(s) {
        try {
            return decodeURIComponent(s);
        } catch {
            return s;
        }
    }

    function extractStreamUrls(text) {
        const out = new Set();
        if (!text) return out;
        const decoded = decodeLoose(text);
        for (const re of STREAM_PATTERNS) {
            re.lastIndex = 0;
            let m;
            while ((m = re.exec(decoded)) !== null) {
                let u = m[0].replace(/\\u002F/gi, '/').replace(/\\\//g, '/');
                if (/logo\.|favicon|\.css|\.js\?|analytics/i.test(u)) continue;
                if (u.length < 20) continue;
                out.add(u);
            }
        }
        return out;
    }

    function pageMeta() {
        const title =
            document.querySelector('h1')?.textContent?.trim() ||
            document.querySelector('.title')?.textContent?.trim() ||
            document.title;
        let thumbnail = null;
        for (const img of document.querySelectorAll('img[src]')) {
            const src = img.src || '';
            if (/img\.supjav\.com\/images\//i.test(src) && !/logo/i.test(src)) {
                thumbnail = src;
                break;
            }
        }
        return { title, thumbnail };
    }

    function notifyUrl(url) {
        if (sent.has(url)) return;
        if (/supjav\.com\/ja\/\d+\.html/i.test(url)) return;
        sent.add(url);
        const meta = pageMeta();
        chrome.runtime
            .sendMessage({
                type: 'STREAM_URL_FOUND',
                url: url,
                title: meta.title,
                thumbnail: meta.thumbnail,
                pageUrl: location.href,
            })
            .catch(() => {});
        console.log('[MATRIX-M] Supjav stream:', url.substring(0, 90));
    }

    function collectText() {
        let blob = '';
        try {
            blob += document.documentElement.innerHTML || '';
        } catch (e) {}
        document.querySelectorAll('script').forEach((s) => {
            blob += s.textContent || '';
        });
        document.querySelectorAll('[data-src],[data-url],[data-video],[data-file]').forEach((el) => {
            ['data-src', 'data-url', 'data-video', 'data-file'].forEach((attr) => {
                const v = el.getAttribute(attr);
                if (v) blob += ' ' + v + ' ';
            });
        });
        document.querySelectorAll('iframe[src]').forEach((f) => {
            blob += ' ' + (f.src || '') + ' ';
        });
        for (const v of document.querySelectorAll('video')) {
            const s = v.currentSrc || v.src || '';
            if (s && !s.startsWith('blob:')) blob += ' ' + s + ' ';
        }
        return blob;
    }

    function scan() {
        const urls = extractStreamUrls(collectText());
        urls.forEach(notifyUrl);
    }

    function scheduleScan() {
        if (scanTimer) clearTimeout(scanTimer);
        scanTimer = setTimeout(scan, 400);
    }

    scan();
    [1500, 4000, 8000, 15000, 25000].forEach((ms) => setTimeout(scan, ms));

    const mo = new MutationObserver(scheduleScan);
    if (document.body) {
        mo.observe(document.body, { childList: true, subtree: true, attributes: true });
    }

    document.addEventListener('play', scheduleScan, true);
    document.addEventListener('loadedmetadata', scheduleScan, true);

    console.log('[MATRIX-M] Supjav watch scanner active');
})();
