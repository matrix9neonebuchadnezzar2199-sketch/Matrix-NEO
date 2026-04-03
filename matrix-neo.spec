# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for MATRIX-NEO (run from MATRIX-NEO folder).
# App version: app/__init__.py __version__ (collect_submodules('app') pulls app package).

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

datas_uv, binaries_uv, hiddenimports_uv = collect_all("uvicorn")
datas_fa, binaries_fa, hiddenimports_fa = collect_all("fastapi")

a = Analysis(
    ['run_server.py'],
    pathex=[],
    binaries=binaries_uv + binaries_fa,
    datas=datas_uv + datas_fa,
    hiddenimports=list(
        set(
            [
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'main',
            ]
            + hiddenimports_uv
            + hiddenimports_fa
            + collect_submodules('app')
        )
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MATRIX-NEO-Server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MATRIX-NEO-Server',
)
