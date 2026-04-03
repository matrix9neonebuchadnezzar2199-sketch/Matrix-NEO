// MATRIX-M Content Script - Quick Add from Thumbnails
(function() {
    'use strict';
    
    const SITE_CONFIG = {
        'dmm.co.jp': {
            thumbSelector: '.thumb-wrap, .tmb, [class*="thumb"], a[href*="/digital/"] img, .d-item img',
            linkSelector: 'a[href*="/digital/"], a[href*="/mono/"]',
            titleSelector: '.txt, .title, [class*="title"]',
            getVideoUrl: (link) => link.href,
            getTitle: (el) => el.querySelector('.txt, .title, [class*="title"]')?.textContent?.trim() || el.querySelector('img')?.alt || ''
        },
        'fanza.com': {
            thumbSelector: '.thumb-wrap, .tmb, [class*="thumb"] img',
            linkSelector: 'a[href*="/digital/"]',
            titleSelector: '.txt, .title',
            getVideoUrl: (link) => link.href,
            getTitle: (el) => el.querySelector('.txt, .title')?.textContent?.trim() || el.querySelector('img')?.alt || ''
        },
        'missav.com': {
            thumbSelector: '.thumbnail img, article img, .video-item img',
            linkSelector: 'a[href*="/video/"], article a',
            titleSelector: '.title, h3, h4',
            getVideoUrl: (link) => link.href,
            getTitle: (el) => el.querySelector('.title, h3, h4')?.textContent?.trim() || el.querySelector('img')?.alt || ''
        },
        'pornhub.com': {
            thumbSelector: '.phimage img, .thumb img',
            linkSelector: 'a[href*="viewkey="]',
            titleSelector: '.title a, span.title',
            getVideoUrl: (link) => link.href,
            getTitle: (el) => el.querySelector('.title a, span.title')?.textContent?.trim() || el.querySelector('img')?.alt || ''
        },
        'xvideos.com': {
            thumbSelector: '.thumb img',
            linkSelector: 'a[href*="/video"]',
            titleSelector: '.title a, p.title',
            getVideoUrl: (link) => link.href,
            getTitle: (el) => el.querySelector('.title a, p.title')?.textContent?.trim() || el.querySelector('img')?.alt || ''
        },
        'xhamster.com': {
            thumbSelector: '.thumb-image-container img',
            linkSelector: 'a[href*="/videos/"]',
            titleSelector: '.video-title',
            getVideoUrl: (link) => link.href,
            getTitle: (el) => el.querySelector('.video-title')?.textContent?.trim() || el.querySelector('img')?.alt || ''
        }
    };
    
    let queue = [];
    let processedElements = new WeakSet();
    
    function getCurrentSite() {
        const hostname = window.location.hostname;
        for (const site in SITE_CONFIG) {
            if (hostname.includes(site)) {
                return SITE_CONFIG[site];
            }
        }
        return null;
    }
    
    function createOverlay(parentEl, videoUrl, title, thumbnail) {
        if (processedElements.has(parentEl)) return;
        processedElements.add(parentEl);
        
        // Make parent relative for overlay positioning
        const style = window.getComputedStyle(parentEl);
        if (style.position === 'static') {
            parentEl.style.position = 'relative';
        }
        parentEl.classList.add('matrix-m-container');
        
        // Create overlay
        const overlay = document.createElement('div');
        overlay.className = 'matrix-m-overlay';
        
        // Create add button
        const addBtn = document.createElement('button');
        addBtn.className = 'matrix-m-add-btn';
        addBtn.title = 'Add to MATRIX-NEO Queue';
        
        addBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            
            if (!addBtn.classList.contains('added')) {
                addToQueue(videoUrl, title, thumbnail);
                addBtn.classList.add('added');
                showNotification(`Added: ${title.substring(0, 30)}...`);
            }
        });
        
        overlay.appendChild(addBtn);
        
        // Add title tooltip
        if (title) {
            const titleEl = document.createElement('div');
            titleEl.className = 'matrix-m-title';
            titleEl.textContent = title;
            overlay.appendChild(titleEl);
        }
        
        parentEl.appendChild(overlay);
    }
    
    function addToQueue(url, title, thumbnail) {
        console.log('[MATRIX-M] addToQueue called:', { url, title, thumbnail });
        const item = {
            id: Date.now().toString(),
            pageUrl: url,
            title: title || 'Unknown Video',
            thumbnail: thumbnail || '',
            addedAt: new Date().toISOString(),
            status: 'analyzing'
        };

        queue.push(item);
        // updateQueueBadge();

        // Send to background script for analysis
        chrome.runtime.sendMessage({
            type: 'ANALYZE_AND_QUEUE',
            data: item
        }).catch(() => {});
    }
    
    function updateQueueBadge() {
        let badge = document.querySelector('.matrix-m-queue-badge');
        
        if (queue.length === 0) {
            if (badge) badge.remove();
            return;
        }
        
        if (!badge) {
            badge = document.createElement('div');
            badge.className = 'matrix-m-queue-badge';
            badge.addEventListener('click', () => {
                chrome.runtime.sendMessage({ type: 'OPEN_SIDEPANEL' }).catch(() => {});
            });
            document.body.appendChild(badge);
        }
        
        badge.textContent = `MATRIX-NEO: ${queue.length} in queue`;
    }
    
    function showNotification(message) {
        const notification = document.createElement('div');
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: linear-gradient(135deg, #00ff88 0%, #00d4ff 100%);
            color: #0a1628;
            padding: 12px 20px;
            border-radius: 8px;
            font-family: 'Segoe UI', sans-serif;
            font-size: 14px;
            font-weight: bold;
            box-shadow: 0 0 30px rgba(0, 255, 136, 0.5);
            z-index: 999999;
            animation: fadeInOut 2s ease;
        `;
        notification.textContent = message;
        document.body.appendChild(notification);
        
        setTimeout(() => notification.remove(), 2000);
    }
    
    function scanPage() {
        const config = getCurrentSite();
        if (!config) return;
        
        // Find all thumbnail containers
        const links = document.querySelectorAll(config.linkSelector);
        
        links.forEach(link => {
            const thumb = link.querySelector('img') || link.closest('div')?.querySelector('img');
            if (!thumb) return;
            
            const container = thumb.closest('a') || thumb.parentElement;
            if (!container || processedElements.has(container)) return;
            
            const videoUrl = config.getVideoUrl(link);
            const title = config.getTitle(link.closest('div, article, li') || link);
            const thumbnail = thumb.src || thumb.dataset.src || '';
            
            if (videoUrl) {
                createOverlay(container, videoUrl, title, thumbnail);
            }
        });
    }
    
    // Initial scan
    setTimeout(scanPage, 1000);
    
    // Re-scan on dynamic content load
    const observer = new MutationObserver((mutations) => {
        let shouldScan = false;
        for (const mutation of mutations) {
            if (mutation.addedNodes.length > 0) {
                shouldScan = true;
                break;
            }
        }
        if (shouldScan) {
            setTimeout(scanPage, 500);
        }
    });
    
    observer.observe(document.body, {
        childList: true,
        subtree: true
    });
    
    // Load existing queue from storage
    chrome.storage.local.get(['matrixQueue'], (result) => {
        queue = result.matrixQueue || [];
        // updateQueueBadge();
    });
    
    // Add animation keyframes
    const style = document.createElement('style');
    style.textContent = `
        @keyframes fadeInOut {
            0% { opacity: 0; transform: translateY(-20px); }
            20% { opacity: 1; transform: translateY(0); }
            80% { opacity: 1; transform: translateY(0); }
            100% { opacity: 0; transform: translateY(-20px); }
        }
    `;
    document.head.appendChild(style);
    
    console.log('[MATRIX-M] Content script loaded');
})();
