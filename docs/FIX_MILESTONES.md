# MATRIX-NEO 修正マイルストーン記録

監査（2026-05-23）に基づき M1〜M4 を実装。各フェーズは **修正 → 本記録 → 次フェーズ** の順で完了。

---

## M1 — 連続 DL 安定化 ✅

### 修正内容

| 領域 | 変更 |
|------|------|
| サーバー | `app/utils/url_normalize.py` — URL 正規化キー |
| サーバー | `app/utils/filename_allocate.py` — `_{task_id[:8]}` 付与で出力衝突防止 |
| サーバー | `app/services/task_dispatch.py` — in-flight 去重・`queue_download_task` |
| サーバー | `app/state.py` — `_in_flight` マップ、`find_in_flight_task` / bind / release |
| サーバー | `app/routes/download.py`, `youtube.py` — 共通ディスパッチ |
| サーバー | `download_service.run_download` — セマフォは DL のみ、後処理は外側 |
| サーバー | `thumbnail_service` — `THUMB_QUEUE_DELAY` を worker 側へ移動 |
| 拡張 | `sidepanel.js` — `pendingDownloadKeys`、`res.ok`、エラー時 UI 維持 |

### 検証

- `tests/test_url_normalize.py`, `test_filename_allocate.py`, `test_download_dedup.py`
- **76 passed**（2026-05-23 時点）

---

## M2 — UI とサーバー同期 ✅

### 修正内容

| 領域 | 変更 |
|------|------|
| 拡張 | `loadVideos` — `clear()` 廃止、マージ更新のみ再描画 |
| 拡張 | `loadServerTasks` — タスク Map のマージ、ポーリング 30s |
| 拡張 | `isVideoDownloading` — `extractVideoIdFromUrl` で DL 中判定統一 |
| 拡張 | `mergeServerTasksFromApi` — SSE との競合軽減 |

---

## M3 — キュー機能 ✅

### 修正内容

| 領域 | 変更 |
|------|------|
| 拡張 | `content.js` — `matrixQueue` へ永続化 + `QUEUE_UPDATED` |
| 拡張 | `background.js` — `MAX_ANALYZE_TABS=2`、完了/失敗で queue から削除 |
| 拡張 | `sidepanel.js` — `QUEUE_FAILED` 表示、「順次DL」ボタン |

---

## M4 — API 整理・品質 ✅

### 修正内容

| 領域 | 変更 |
|------|------|
| サーバー | `stop_resume.py` — `.part` 保持、``clear-stopped`` / ``clear-finished`` 分離 |
| サーバー | `events.py`, `tasks_read.py` — `all_tasks_snapshot()` |
| サーバー | resume — `bind_in_flight` / `release_in_flight` |
| 拡張 | `popup.js` — `task_id` 応答・Bearer 対応 |
| 拡張 | `clearCompletedTasks` → `/tasks/clear-finished` |

### 残タスク（意図的に後回し）

- yt-dlp 経路の HLS 同等サムネキュー
- `BLOCK_PRIVATE_IPS` デフォルト変更
- `/health` へのディスク・active 数拡張

---

## 変更ファイル一覧

```
app/utils/url_normalize.py          (新規)
app/utils/filename_allocate.py      (新規)
app/services/task_dispatch.py       (新規)
app/state.py
app/routes/download.py
app/routes/youtube.py
app/routes/stop_resume.py
app/routes/events.py
app/routes/tasks_read.py
app/services/download_service.py
app/services/youtube_service.py
app/services/thumbnail_service.py
extension/sidepanel.js
extension/content.js
extension/background.js
extension/popup.js
tests/test_url_normalize.py         (新規)
tests/test_filename_allocate.py     (新規)
tests/test_download_dedup.py        (新規)
docs/FIX_MILESTONES.md              (本ファイル)
```
