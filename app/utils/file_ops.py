"""Atomic file replace with cross-device fallback."""

from __future__ import annotations

import os
import shutil


def replace_or_move_overwrite(src: str, dst: str) -> None:
    try:
        os.replace(src, dst)
    except OSError:
        shutil.copy2(src, dst)
        try:
            os.remove(src)
        except OSError:
            pass
