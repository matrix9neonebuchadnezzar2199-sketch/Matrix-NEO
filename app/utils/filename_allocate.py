"""Allocate collision-free output filenames on disk."""

from __future__ import annotations

import re

from app.utils.filename import sanitize_filename_for_windows


def unique_output_filename(
    requested: str | None,
    task_id: str,
    *,
    ext: str = ".mp4",
) -> str:
    """
    Append a short task id suffix so concurrent downloads with the same title
    do not overwrite each other in output/.
    """
    ext_l = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
    raw = (requested or f"video_{task_id}").strip()
    if not raw:
        raw = f"video_{task_id}"

    stem = raw
    if stem.lower().endswith(ext_l):
        stem = stem[: -len(ext_l)]

    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")[:80]
    if not stem:
        stem = "video"

    suffix = task_id[:8]
    if stem.endswith(f"_{suffix}"):
        final = f"{stem}{ext_l}"
    else:
        final = f"{stem}_{suffix}{ext_l}"

    if ext_l == ".mp4":
        return sanitize_filename_for_windows(final)
    # mp3 and other extensions: same forbidden char rules, no bracket swap required
    final = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", final)
    final = final.replace("[", "（").replace("]", "）")
    if not final.lower().endswith(ext_l):
        final = f"{final}{ext_l}" if not final.endswith(".") else final + ext_l.lstrip(".")
    return final or f"video_{suffix}{ext_l}"
