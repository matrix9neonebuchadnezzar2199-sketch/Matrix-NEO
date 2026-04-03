"""
MATRIX-NEO サーバー起動エントリ。
開発時: python run_server.py
配布時: PyInstaller で MATRIX-NEO-Server.exe に固める。
"""
from __future__ import annotations

import multiprocessing


def main() -> None:
    import uvicorn
    from main import app

    uvicorn.run(app, host="127.0.0.1", port=58500, log_level="info")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
