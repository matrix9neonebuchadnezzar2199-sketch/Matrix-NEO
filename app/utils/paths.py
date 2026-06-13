"""Executable and tools path resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.config import BASE_DIR

_TOOL_ENV_KEYS: dict[str, str] = {
    "ffmpeg": "MATRIX_NEO_FFMPEG",
    "yt-dlp": "MATRIX_NEO_YTDLP",
    "N_m3u8DL-RE": "MATRIX_NEO_M3U8DL",
}


def _env_tool_path(env_key: str) -> str | None:
    raw = os.environ.get(env_key, "").strip().strip('"')
    if not raw:
        return None
    p = Path(raw)
    if p.is_file():
        return str(p.resolve())
    return None


def _is_valid_tool_file(path: Path) -> bool:
    if not path.is_file():
        return False
    s = str(path).replace("\\", "/")
    return "__MACOSX" not in s and not path.name.startswith("._")


def _find_in_versioned_bundles(tools: Path, stem: str) -> str | None:
    """Prefer tools/<stem>-*/bin/<stem>.exe (Windows essentials / full builds)."""
    name = f"{stem}.exe" if sys.platform == "win32" else stem
    found: list[Path] = []
    try:
        for sub in tools.iterdir():
            if not sub.is_dir():
                continue
            if not sub.name.lower().startswith(f"{stem.lower()}-"):
                continue
            cand = sub / "bin" / name
            if _is_valid_tool_file(cand):
                found.append(cand)
    except OSError:
        return None
    if not found:
        return None
    # Prefer semver-like folder names (ffmpeg-8.1.1) then newest dated git builds.
    def sort_key(p: Path) -> tuple:
        parts = p.parts
        folder = parts[-3] if len(parts) >= 3 else ""
        ver = folder.split("-", 1)[-1] if "-" in folder else folder
        return (ver, folder)

    found.sort(key=sort_key, reverse=True)
    return str(found[0])


def tool_path(stem: str) -> str:
    env_key = _TOOL_ENV_KEYS.get(stem)
    if env_key:
        from_env = _env_tool_path(env_key)
        if from_env:
            return from_env

    tools = BASE_DIR / "tools"
    name = f"{stem}.exe" if sys.platform == "win32" else stem
    if sys.platform == "win32":
        for cand in (tools / name, tools / stem):
            if _is_valid_tool_file(cand):
                return str(cand)
    else:
        p = tools / stem
        if _is_valid_tool_file(p):
            return str(p)

    bundled = _find_in_versioned_bundles(tools, stem)
    if bundled:
        return bundled

    try:
        found = []
        for p in tools.rglob(name):
            if _is_valid_tool_file(p):
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
