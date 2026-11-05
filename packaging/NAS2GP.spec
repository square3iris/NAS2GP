# -*- mode: python ; coding: utf-8 -*-
# ============================================
# FILE: packaging/NAS2GP.spec
#
# NAS2GP 配布物 (onedir) を PyInstaller で作成する spec ファイル。
#   - 生成物: dist/NAS2GP/NAS2GP.exe (+ _internal/) と、そのフォルダ一式。
#   - config / database / logs は exe の隣に初回起動時に自動生成される
#     (launcher.py が WORK_ROOT を exe フォルダへ固定する)。
#   - ADB は同梱しない (ユーザーが別途導入。未検出時は launcher が案内)。
#
# PyInstaller spec that builds the NAS2GP onedir distribution.
#   - Output: dist/NAS2GP/NAS2GP.exe (+ _internal/) and the surrounding folder.
#   - config / database / logs are created next to the exe on first launch
#     (launcher.py pins WORK_ROOT to the exe folder).
#   - ADB is not bundled (install separately; the launcher guides when missing).
#
# ビルド / Build:
#   python source/scripts/build_app.py     (推奨 / recommended)
#   python -m PyInstaller --noconfirm --clean packaging/NAS2GP.spec
# ============================================

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# SPECPATH は PyInstaller が渡す spec ファイルのディレクトリ (packaging/)。
# SPECPATH is the spec file's directory (packaging/), provided by PyInstaller.
ROOT = Path(SPECPATH).resolve().parent          # リポジトリ ルート / repository root
SRC = ROOT / "source"

# uvicorn は動的 import が多く、自動解析だけでは取りこぼすため全サブモジュールを収録する。
# (WebSocket 系は未使用。auto 実装が try/except で保護するため同梱しても害はない。)
# uvicorn relies on dynamic imports; collect all submodules to be safe.
# (WebSockets are unused; the auto implementation guards them with try/except.)
hiddenimports = sorted(
    set(
        collect_submodules("uvicorn")
        + collect_submodules("anyio")
        + [
            "fastapi",
            "starlette",
            "jinja2",
            "pydantic",
            # launcher.py が関数内で import するため明示的に収録する。
            # Imported inside a function in launcher.py, so list it explicitly.
            "entry_web",
        ]
    )
)

# Web UI のテンプレート (HTML) はパッケージ内データとして同梱する。
# The Web UI templates (HTML) ship as package data.
datas = [
    (str(SRC / "nas2gp" / "ui_web" / "templates"), "nas2gp/ui_web/templates"),
]

# アイコンは任意 (packaging/NAS2GP.ico があれば使う)。
# Icon is optional (used when packaging/NAS2GP.ico exists).
_icon = ROOT / "packaging" / "NAS2GP.ico"
icon = str(_icon) if _icon.exists() else None

a = Analysis(
    [str(SRC / "launcher.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "Pyside6",
        "PySide6",
        "PyQt5",
        "PyQt6",
        "matplotlib",
        "numpy",
        "pandas",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NAS2GP",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="NAS2GP",
)
