"""Windows-safe filenames."""

from __future__ import annotations

import os
import re


def is_ascii_basename(path: str) -> bool:
    try:
        os.path.basename(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def sanitize_filename_for_windows(name: str) -> str:
    """
    Windows ではファイル名の [ ] がディレクトリ走査でワイルドカード扱いになり、
    N_m3u8DL の最終リネーム（*.copy.mp4 等）が失敗して moov の無い壊れた MP4 になることがある。
    <>:"/\\|?* および制御文字も NTFS で問題になりうるため除去・置換する。
    """
    if not name or not str(name).strip():
        return "video.mp4"
    name = str(name).strip()
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name)
    name = name.replace("[", "（").replace("]", "）")
    name = name.strip(" .")
    if not name.lower().endswith(".mp4"):
        name = f"{name}.mp4"
    if name.lower() == ".mp4" or len(name) < 5:
        name = "video.mp4"
    return name
