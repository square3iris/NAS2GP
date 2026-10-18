#!/usr/bin/env python3
# ============================================
# FILE: source/scripts/build_release.py
#
# 配布用 ZIP (archive/NAS2GP_v<version>.zip) を作成する ビルドスクリプト 。
# Build script that creates the distribution ZIP (archive/NAS2GP_v<version>.zip).
#
# 使い方 / Usage:
#   python source/scripts/build_release.py
#
# 収録対象 / Included:
#   git 管理下 の ファイル (git ls-files。.gitignore で 個人データ は 既に 除外 済み) 。
#   Git-tracked files (git ls-files; personal data is already excluded by .gitignore).
#
# 除外 / Excluded:
#   - archive/           : 過去 の 配布 ZIP (ZIP の 入れ子 を 避ける)
#   - docs/*/ の 一部    : SETTINGS.md と OPERATION_GUIDE.md 以外 (開発者 向け は 配布 不要)
#   - archive/           : previous release ZIPs (avoid nesting ZIPs)
#   - most of docs/*/    : only SETTINGS.md and OPERATION_GUIDE.md are user-facing
# ============================================

from __future__ import annotations

import re
import subprocess
import zipfile
from pathlib import Path

# リポジトリ ルート 。 この ファイル は source/scripts/ 配下 に ある 。
# Repository root. This file lives under source/scripts/.
REPO_ROOT = Path(__file__).resolve().parents[2]

# 配布 ZIP に 含めない パス (先頭 一致) 。
# Path prefixes excluded from the distribution ZIP (prefix match).
EXCLUDE_PREFIXES = ("archive/",)

# 配布 ZIP に 含める docs の ファイル名 。 これ 以外 の docs は 除外 する 。
# Doc filenames kept in the distribution ZIP; all other docs are excluded.
ALLOWED_DOCS = ("SETTINGS.md", "OPERATION_GUIDE.md")


def read_version() -> str:
    """settings.py の APP_VERSION を 唯一 の バージョン 情報源 と して 読む 。
    Read APP_VERSION from settings.py as the single source of truth."""
    settings = REPO_ROOT / "source" / "nas2gp" / "core" / "settings.py"
    text = settings.read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise SystemExit("APP_VERSION を settings.py から 読み取れ ません でした 。")
    return m.group(1)


def list_files() -> list[str]:
    """git 管理下 の ファイル 一覧 を 取得 する (追跡 済み + 未追跡 で 無視 され ない もの) 。
    List git-managed files (tracked + untracked-and-not-ignored)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"git ls-files の 実行 に 失敗 し まし た : {exc}")
    return [line for line in out.stdout.splitlines() if line.strip()]


def is_included(rel: str) -> bool:
    """配布 ZIP に 含める か 判定 する 。 / Whether to include the path."""
    if any(rel.startswith(p) for p in EXCLUDE_PREFIXES):
        return False
    if rel.startswith("docs/"):
        return rel.rsplit("/", 1)[-1] in ALLOWED_DOCS
    return True


def main() -> int:
    version = read_version()
    root_name = f"NAS2GP_v{version}"
    out_path = REPO_ROOT / "archive" / f"{root_name}.zip"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(f for f in list_files() if is_included(f))
    if not files:
        raise SystemExit("配布 対象 の ファイル が 見つかり ません でした 。")

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            zf.write(REPO_ROOT / rel, f"{root_name}/{rel}")

    print(f"created : {out_path}")
    print(f"version : {version}")
    print(f"files   : {len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
