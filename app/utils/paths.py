"""Executable and tools path resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.config import BASE_DIR


def tool_path(stem: str) -> str:
    tools = BASE_DIR / "tools"
    name = f"{stem}.exe" if sys.platform == "win32" else stem
    if sys.platform == "win32":
        for cand in (tools / name, tools / stem):
            if cand.is_file():
                return str(cand)
    else:
        p = tools / stem
        if p.is_file():
            return str(p)
    try:
        found = []
        for p in tools.rglob(name):
            s = str(p).replace("\\", "/")
            if "__MACOSX" in s or p.name.startswith("._"):
                continue
            found.append(p)
        if found:
            found.sort(key=lambda x: len(x.parts))
            return str(found[0])
    except OSError:
        pass
    return stem


N_M3U8DL_RE = tool_path("N_m3u8DL-RE")
YTDLP = tool_path("yt-dlp")
FFMPEG = tool_path("ffmpeg")
