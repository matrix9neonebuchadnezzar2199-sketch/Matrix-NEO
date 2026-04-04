// MATRIX-NEO Shared Utility Functions
// Extracted from sidepanel.js for maintainability.

/**
 * Escape HTML special characters to prevent XSS.
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/**
 * Extract a stable video ID from a URL for duplicate detection.
 * @param {string} url
 * @returns {string}
 */
function extractVideoIdFromUrl(url) {
    try {
        const u = new URL(url);
        if (u.searchParams.has('v')) return 'yt:' + u.searchParams.get('v');
        const uuidMatch = u.pathname.match(
            /([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i
        );
        if (uuidMatch) return 'uuid:' + uuidMatch[1];
        const cleanPath = u.pathname
            .replace(/\/(1080p|720p|480p|360p)\/video\.m3u8.*$/, '')
            .replace(/\/playlist\.m3u8.*$/, '');
        return u.origin + cleanPath;
    } catch {
        return url;
    }
}

/**
 * Check if a URL has already been downloaded.
 * @param {string} videoUrl
 * @param {Array} history
 * @returns {boolean}
 */
function isDownloadedUrl(videoUrl, history) {
    const targetId = extractVideoIdFromUrl(videoUrl);
    return history.some((item) => extractVideoIdFromUrl(item.url) === targetId);
}

/**
 * Normalize a URL by stripping fragment.
 * @param {string} u
 * @returns {string}
 */
function normalizeUrl(u) {
    if (!u) return '';
    try {
        const x = new URL(u);
        return x.href.replace(/#.*$/, '');
    } catch {
        return u;
    }
}

/**
 * Format seconds into human-readable duration (H:MM:SS or M:SS).
 * @param {number|null} sec
 * @returns {string}
 */
function formatDurationLabel(sec) {
    if (sec == null || typeof sec !== 'number' || !Number.isFinite(sec) || sec <= 0) {
        return '';
    }
    const s = Math.floor(sec);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    if (h > 0) {
        return h + ':' + String(m).padStart(2, '0') + ':' + String(r).padStart(2, '0');
    }
    return m + ':' + String(r).padStart(2, '0');
}

/**
 * Extract a video identifier from a filename (FC2, product codes, etc.).
 * @param {string} filename
 * @returns {string}
 */
function extractVideoIdFromFilename(filename) {
    const patterns = [
        /FC2-PPV-(\d+)/i,
        /FC2PPV-(\d+)/i,
        /FC2-(\d+)/i,
        /([A-Z]+-\d+)/i,
        /(\d{6,})/,
    ];
    for (const pattern of patterns) {
        const match = filename.match(pattern);
        if (match) return match[0].toUpperCase().replace('FC2PPV', 'FC2-PPV');
    }
    return filename.replace(/\.[^.]+$/, '').substring(0, 30);
}

/**
 * Convert a two-letter country code to its flag emoji.
 * @param {string} countryCode
 * @returns {string}
 */
function getCountryFlag(countryCode) {
    if (!countryCode || countryCode.length !== 2) return '';
    const offset = 127397;
    const firstChar = countryCode.toUpperCase().charCodeAt(0);
    const secondChar = countryCode.toUpperCase().charCodeAt(1);
    return (
        String.fromCodePoint(firstChar + offset) +
        String.fromCodePoint(secondChar + offset)
    );
}
