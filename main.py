"""
Backward-compatible entry: `from main import app` (PyInstaller / old scripts).

The application lives in `app.main`.
"""

from app.main import app  # noqa: F401
