#!/usr/bin/env python3
# ============================================
# FILE: source/scripts/build_app.py
#
# 配布用アプリ (onedir) を PyInstaller で作成し、ZIP にまとめる ビルドスクリプト。
#   - 生成物: build/app/dist/NAS2GP/ (NAS2GP.exe と _internal/)
#   - ZIP   : archive/NAS2GP_v<version>_<os>.zip
#     (Windows のみ検証済み。macOS / Linux はベストエフォート)
#
# Build script that creates the distribution app (onedir) with PyInstaller and
# packs it into a ZIP.
#   - Output: build/app/dist/NAS2GP/ (NAS2GP.exe and _internal/)
#   - ZIP   : archive/NAS2GP_v<version>_<os>.zip
#     (verified on Windows only; macOS / Linux are best-effort)
#
# 前提 / Prerequisites:
#   pip install -r requirements.txt
#   pip install pyinstaller
#
# 使い方 / Usage:
#   python source/scripts/build_app.py
# ============================================

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

# リポジトリ ルート 。 この ファイル は source/scripts/ 配下 に ある 。
# Repository root. This file lives under source/scripts/.
REPO_ROOT = Path(__file__).resolve().parents[2]

SPEC_FILE = REPO_ROOT / "packaging" / "NAS2GP.spec"
BUILD_DIR = REPO_ROOT / "build" / "app"
DIST_DIR = BUILD_DIR / "dist"
WORK_DIR = BUILD_DIR / "work"
ARCHIVE_DIR = REPO_ROOT / "archive"


def read_version() -> str:
    """settings.py の APP_VERSION を 唯一 の バージョン 情報源 と して 読む 。
    Read APP_VERSION from settings.py as the single source of truth."""
    settings = REPO_ROOT / "source" / "nas2gp" / "core" / "settings.py"
    text = settings.read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise SystemExit("APP_VERSION を settings.py から 読み取れ ません でした 。")
    return m.group(1)


def platform_tag() -> str:
    """ZIP ファイル名 に 付ける OS タグ を 返す。
    Return the OS tag used in the ZIP filename."""
    if sys.platform.startswith("win"):
        return "win64"
    if sys.platform == "darwin":
        return "macos"
    return "linux64"


def main() -> int:
    if not SPEC_FILE.exists():
        raise SystemExit(f"spec ファイル が 見つかり ませ ん : {SPEC_FILE}")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("PyInstaller が 未 インストール です 。 pip install pyinstaller を 実行 し て ください 。")

    version = read_version()
    tag = platform_tag()
    print(f"[build_app] version={version} platform={tag}")
    print(f"[build_app] pyinstaller で ビルド 中 ... (数分 かかる 場合 が あり ます)")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(WORK_DIR),
        str(SPEC_FILE),
    ]
    try:
        subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"PyInstaller の ビルド に 失敗 し まし た (exit={exc.returncode})。")

    app_dir = DIST_DIR / "NAS2GP"
    if not app_dir.is_dir():
        raise SystemExit(f"ビルド 成果物 が 見つかり ませ ん : {app_dir}")

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ARCHIVE_DIR / f"NAS2GP_v{version}_{tag}.zip"
    root_name = f"NAS2GP_v{version}"

    files = sorted(p for p in app_dir.rglob("*") if p.is_file())
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            rel = p.relative_to(app_dir)
            zf.write(p, f"{root_name}/{rel.as_posix()}")

    print(f"[build_app] created : {out_path}")
    print(f"[build_app] files   : {len(files)}")
    print(f"[build_app] app dir : {app_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
