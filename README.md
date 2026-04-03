# MATRIX-NEO（PyInstaller 配布用）

このフォルダは親プロジェクト MATRIX-M とは独立して運用します。  
MATRIX-M の `app/main.py` をコピーし、exe 同梱向けにパス解決を追加した `main.py` を含みます。

## フォルダ構成（想定）

| パス | 説明 |
|------|------|
| `main.py` | FastAPI アプリ本体 |
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

→ <http://127.0.0.1:6850/health>

## exe のビルド（Windows）

`build-windows.bat` を実行すると、`dist\MATRIX-NEO-Server\MATRIX-NEO-Server.exe` が生成されます。

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
- ポートは **6850**（`main.py` / `run_server.py` と揃えています）。

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
