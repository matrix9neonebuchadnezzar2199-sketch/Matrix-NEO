// MATRIX-M Popup Script

class MatrixM {
  constructor() {
    this.serverUrl = 'http://localhost:6850';
    this.detectedVideos = [];
    this.init();
  }

  async init() {
    await this.loadSettings();
    this.bindEvents();
    this.checkServerStatus();
    this.loadDetectedVideos();
  }

  async loadSettings() {
    try {
      const result = await chrome.storage.local.get(['serverUrl']);
      if (result.serverUrl) {
        this.serverUrl = result.serverUrl;
        document.getElementById('serverUrl').value = this.serverUrl;
      }
    } catch (e) {
      console.error('設定読み込みエラー:', e);
    }
  }

  bindEvents() {
    // 設定トグル
    document.getElementById('toggleSettings').addEventListener('click', () => {
      const panel = document.getElementById('settingsPanel');
      panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
    });

    // 設定保存
    document.getElementById('saveSettings').addEventListener('click', () => {
      this.saveSettings();
    });

    // 接続テスト
    document.getElementById('testConnection').addEventListener('click', () => {
      this.checkServerStatus(true);
    });

    // バックグラウンドからのメッセージ受信
    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
      if (message.type === 'VIDEO_DETECTED') {
        this.addVideo(message.data);
      } else if (message.type === 'VIDEOS_LIST') {
        this.detectedVideos = message.data;
        this.renderVideoList();
      }
    });
  }

  async saveSettings() {
    const serverUrl = document.getElementById('serverUrl').value.trim();
    if (!serverUrl) {
      alert('サーバーURLを入力してください');
      return;
    }

    this.serverUrl = serverUrl;
    await chrome.storage.local.set({ serverUrl });
    this.checkServerStatus(true);
  }

  async checkServerStatus(showAlert = false) {
    const statusDot = document.querySelector('.status-dot');
    const statusText = document.querySelector('.status-text');

    try {
      const response = await fetch(`${this.serverUrl}/health`, {
        method: 'GET',
        mode: 'cors',
      });

      if (response.ok) {
        statusDot.className = 'status-dot online';
        statusText.textContent = 'オンライン';
        if (showAlert) alert('接続成功！');
      } else {
        throw new Error('サーバーエラー');
      }
    } catch (e) {
      statusDot.className = 'status-dot offline';
      statusText.textContent = 'オフライン';
      if (showAlert) alert('接続失敗: ' + e.message);
    }
  }

  async loadDetectedVideos() {
    try {
      const response = await chrome.runtime.sendMessage({ type: 'GET_VIDEOS' });
      if (response && response.videos) {
        this.detectedVideos = response.videos;
        this.renderVideoList();
      }
    } catch (e) {
      console.error('動画リスト取得エラー:', e);
    }
  }

  addVideo(video) {
    // 重複チェック
    const exists = this.detectedVideos.some(v => v.url === video.url);
    if (!exists) {
      this.detectedVideos.unshift(video);
      this.renderVideoList();
    }
  }

  renderVideoList() {
    const container = document.getElementById('videoList');

    if (this.detectedVideos.length === 0) {
      container.innerHTML = `
        <div class="empty-message">
          <span class="blink">_</span> 動画を検出中...
        </div>
      `;
      return;
    }

    container.innerHTML = this.detectedVideos.map((video, index) => `
      <div class="video-card" data-index="${index}">
        <button class="btn-close" data-action="remove" data-index="${index}">×</button>
        <div class="video-thumbnail">
          ${video.thumbnail 
            ? `<img src="${video.thumbnail}" alt="thumbnail">`
            : 'NO IMAGE'
          }
          ${video.duration ? `<span class="video-duration">${video.duration}</span>` : ''}
        </div>
        <div class="video-info">
          <div class="video-title">
            <span class="video-type">${video.type}</span>
            ${this.escapeHtml(video.title || 'Unknown Video')}
          </div>
          <div class="video-meta">
            <span class="video-format">${video.format || 'MP4'}</span>
            ${video.quality ? `<span class="video-quality">${video.quality}</span>` : ''}
          </div>
          <div class="video-actions">
            <button class="btn-download" data-action="download" data-index="${index}">
              <span class="icon">↓</span> ダウンロード
            </button>
            <button class="btn-menu" data-action="menu" data-index="${index}">⋮</button>
          </div>
          <div class="progress-container" id="progress-${index}" style="display:none;">
            <div class="progress-bar">
              <div class="progress-fill" style="width: 0%"></div>
              <span class="progress-text">0%</span>
            </div>
          </div>
        </div>
      </div>
    `).join('');

    // イベント設定
    container.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const action = e.currentTarget.dataset.action;
        const index = parseInt(e.currentTarget.dataset.index);
        this.handleAction(action, index);
      });
    });
  }

  handleAction(action, index) {
    const video = this.detectedVideos[index];
    if (!video) return;

    switch (action) {
      case 'download':
        this.startDownload(video, index);
        break;
      case 'remove':
        this.removeVideo(index);
        break;
      case 'menu':
        this.showMenu(video, index);
        break;
    }
  }

  async startDownload(video, index) {
    const btn = document.querySelector(`[data-action="download"][data-index="${index}"]`);
    const progressContainer = document.getElementById(`progress-${index}`);

    btn.disabled = true;
    btn.innerHTML = '<span class="icon">◌</span> 送信中...';
    progressContainer.style.display = 'block';

    try {
      const response = await fetch(`${this.serverUrl}/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: video.url,
          filename: video.title || `video_${Date.now()}`,
          format_type: 'mp4'
        })
      });

      const result = await response.json();

      if (result.success) {
        btn.innerHTML = '<span class="icon">✓</span> 開始しました';
        this.trackProgress(result.task_id, index);
      } else {
        throw new Error(result.detail || 'ダウンロード開始失敗');
      }
    } catch (e) {
      btn.disabled = false;
      btn.innerHTML = '<span class="icon">↓</span> ダウンロード';
      progressContainer.style.display = 'none';
      alert('エラー: ' + e.message);
    }
  }

  async trackProgress(taskId, index) {
    const progressContainer = document.getElementById(`progress-${index}`);
    const progressFill = progressContainer.querySelector('.progress-fill');
    const progressText = progressContainer.querySelector('.progress-text');
    const btn = document.querySelector(`[data-action="download"][data-index="${index}"]`);

    const checkStatus = async () => {
      try {
        const response = await fetch(`${this.serverUrl}/status/${taskId}`);
        const status = await response.json();

        progressFill.style.width = `${status.progress}%`;
        progressText.textContent = `${Math.round(status.progress)}%`;

        if (status.status === 'completed') {
          btn.innerHTML = '<span class="icon">✓</span> 完了';
          progressText.textContent = '完了';
        } else if (status.status === 'error') {
          btn.innerHTML = '<span class="icon">✗</span> エラー';
          btn.disabled = false;
          progressText.textContent = 'エラー';
          alert('ダウンロードエラー: ' + (status.error_message || '不明なエラー'));
        } else {
          setTimeout(checkStatus, 1000);
        }
      } catch (e) {
        console.error('進捗確認エラー:', e);
        setTimeout(checkStatus, 2000);
      }
    };

    checkStatus();
  }

  removeVideo(index) {
    this.detectedVideos.splice(index, 1);
    this.renderVideoList();
    // バックグラウンドにも通知
    chrome.runtime.sendMessage({ type: 'REMOVE_VIDEO', index });
  }

  showMenu(video, index) {
    const menu = [
      { label: 'URLをコピー', action: () => navigator.clipboard.writeText(video.url) },
      { label: '音声のみダウンロード', action: () => this.downloadAudioOnly(video, index) },
    ];
    // 簡易メニュー（後で改善可能）
    const choice = confirm(`URLをコピーしますか?\n${video.url}`);
    if (choice) {
      navigator.clipboard.writeText(video.url);
    }
  }

  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
}

// 初期化
document.addEventListener('DOMContentLoaded', () => {
  new MatrixM();
});
