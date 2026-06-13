MATRIX-NEO 用: 次の実行ファイルをこのフォルダに置いてください（Windows 想定）。

  N_m3u8DL-RE.exe   … N_m3u8DL-RE 公式リリースの Windows 版
  yt-dlp.exe        … https://github.com/yt-dlp/yt-dlp/releases
  ffmpeg.exe        … 同梱ビルド（下記 sync-ffmpeg.bat）または gyan.dev の essentials/full build
  AtomicParsley.exe … http://atomicparsley.sourceforge.net/ 等

FFmpeg 更新手順:
  1. tools\ffmpeg-8.1.1-full_build\bin\ などに exe を置く（ffmpeg-8.1.1 はソースのみの場合あり）
  2. tools\sync-ffmpeg.bat を実行 → tools\ffmpeg.exe にコピー
  3. サーバー再起動。/health の ffmpeg_version で確認

WinGet の ffmpeg 7.1.x（PATH）は脆弱性警告の対象。NEO は tools\ffmpeg.exe を優先する。

名前は上記のとおり（.exe）で揃えてください。
無い場合は同名のコマンドが PATH 上にあると仮定します（動作は環境依存・非推奨）。
