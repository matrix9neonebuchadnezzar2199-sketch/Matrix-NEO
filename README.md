# MATRIX-NEO（PyInstaller 配布用）

このフォルダは親プロジェクト MATRIX-M とは独立して運用します。  
サーバー実装は `app/` パッケージに分割され、ルートの `main.py` は `from app.main import app` 互換用の薄いラッパーです。

## フォルダ構成（想定）

| パス | 説明 |
|------|------|
| `app/` | FastAPI アプリ（`routes/`・`services/`・`config.py` など） |
| `main.py` | 後方互換: `app.main.app` の再エクスポート |
| `run_server.py` | uvicorn 起動（PyInstaller のエントリ） |
| `matrix-neo.spec` | PyInstaller 定義 |
| `build-windows.bat` | Windows で exe ビルド |
| `requirements.txt` | 実行に必要な Python パッケージ |
| `tools/` | N_m3u8DL-RE.exe, yt-dlp.exe, ffmpeg.exe, AtomicParsley.exe |
| `output/` | ダウンロード保存先 |
| `temp/` | 一時ファイル |
| `extension/` | Chrome 拡張（パッケージ化されていない拡張機能で読み込み） |

## Python のバージョン

`pip` / `pydantic-core` で Rust ビルドエラーになる場合は、**Python 3.12 または 3.13** で venv を作り直してください。  
（3.14 はパッケージによってはまだホイール未対応のことがあります。）

## 開発時の起動（Docker なし）

```bat
cd 本フォルダ
python -m venv .venv
.venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
```

`tools\` に上記 exe を配置してから:

```bat
python run_server.py
```

→ <http://127.0.0.1:6850/health>（ポートは環境変数 `MATRIX_NEO_PORT` で変更可、既定 **6850**）

## exe のビルド（Windows・任意）

普段の開発は上記の `python run_server.py` のみで足ります。  
配布用に単一 exe が必要なときだけ `build-windows.bat` を実行し、`dist\MATRIX-NEO-Server\MATRIX-NEO-Server.exe` を生成します。

## 配布用 ZIP の作り方

`dist\MATRIX-NEO-Server\` フォルダ全体をベースに、同じ階層に次をコピー:

- `tools\`（各 exe）
- `extension\`（拡張一式）
- `output\`（空で可）
- `temp\`（空で可）

ユーザーは `MATRIX-NEO-Server.exe` をダブルクリックで起動し、Chrome で `extension` フォルダを読み込みます。

## 注意

- 初回のみ Windows ファイアウォールの許可ダイアログが出ることがあります。
- PyInstaller の exe はウイルス対策ソフトに誤検知される場合があります。
- ポートは **6850**（`run_server.py` と `app.config.PORT`）。拡張のサーバー URL も同じポートに合わせてください。

### 主な環境変数

| 変数 | 既定 | 説明 |
|------|------|------|
| `MATRIX_NEO_PORT` | `6850` | 待受ポート |
| `MATRIX_NEO_LOG_LEVEL` | `INFO` | ログレベル |
| `MATRIX_NEO_BLOCK_PRIVATE_IPS` | `0` | `1` でプライベート IP 向け URL を拒否（SSRF 緩和・LAN 再生は阻害）。**リンクローカル / ULA / 169.254.0.0/16（メタデータ等）はフラグに関係なく常に拒否** |
| `MATRIX_NEO_TASK_TTL_HOURS` | `24` | 完了/エラー/**停止**タスクをメモリから削除するまでの時間 |
| `MATRIX_NEO_PROXY_IMAGE_RATE_LIMIT` | `30` | `/proxy-image` のクライアントあたり許可リクエスト数（ウィンドウ内） |
| `MATRIX_NEO_PROXY_IMAGE_RATE_WINDOW_SEC` | `60` | 上記のウィンドウ秒 |
| `MATRIX_NEO_VPN_KEYWORDS` | （既定リスト） | `/vpn-status` の ISP 名判定用キーワード（カンマ区切り） |
| `MAX_CONCURRENT_DOWNLOADS` | `10` | 同時ダウンロード数 |

変数の一覧例はリポジトリ直下の **`.env.example`** を参照してください。機密やローカル上書き用の **`.env`** は Git に含めません。

## トラブル（429 / 中盤で 0.00Bps → Force Exit）

配信側のレート制限やセグメント URL の期限で、止まって N_m3u8DL が諦めることがあります。

既定（最大速度寄り）: `thread=32`・`retry=50`・HTTP 120s・`-mt ON`・`max_speed` なし（無制限）・Chrome 風 UA 等。

速すぎて 429 が出る場合は帯域・並列を下げる:

```powershell
$env:MATRIX_NEO_M3U8_THREADS = "8"
$env:MATRIX_NEO_M3U8_MT = "0"
$env:MATRIX_NEO_M3U8_MAX_SPEED = "8M"
python run_server.py
```

さらに安定優先なら `THREADS=1`、`RETRY` を上げる（例: 80）。  
`MAX_SPEED` は全体の速度上限（空なら無制限）。ブラウザと同じ UA が必要な場合は既定のまま。

無効化: `$env:MATRIX_NEO_M3U8_BROWSER_HEADERS = "0"`
