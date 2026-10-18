# ============================================
# FILE: pixel_sync/core/maintenance.py
# VERSION: 1.0.0 (v1.7.15: Web 管理 ページ 用)
# VERSION: 1.0.0 (v1.7.15: for the Web maintenance page)
# PURPOSE: Web 管理 ページ 用 の リセット 処理 を Qt 非依存 で 提供 する。
# PURPOSE: Provide Qt-independent reset operations for the Web maintenance page.
#   Web UI (/api/maintenance/*) から 呼ぶ 共通ロジック。
#   Shared logic called from the Web UI (/api/maintenance/*).
#   各リセット は 元 ファイル を _OLD_YYYYMMDD_HHMMSS_ 接頭辞 で rename 退避 する。
#   Each reset renames the original file aside with an _OLD_YYYYMMDD_HHMMSS_ prefix.
# ============================================
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

# リセット対象 パス
# Paths targeted for reset
_CONFIG_DIR = Path("config")
_CONFIG_FILE = _CONFIG_DIR / "config_ui.json"


def _work_root() -> Path:
    try:
        from pixel_sync.core.settings import WORK_ROOT
        if WORK_ROOT:
            return Path(str(WORK_ROOT))
    except Exception:
        pass
    try:
        return Path.cwd()
    except Exception:
        return Path(".")


def _resolve(p) -> Path:
    pp = Path(str(p))
    return pp if pp.is_absolute() else (_work_root() / pp).resolve()


def _rename_old(targets) -> Tuple[List[Tuple[Path, Path, str]], List[Tuple[Any, str]]]:
    """対象 を _OLD_{stamp}_{name} へ rename 退避 する (GUI 版 と 同一 方式)。

    Rename targets aside to _OLD_{stamp}_{name} (same approach as the GUI version).
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    renamed: List[Tuple[Path, Path, str]] = []
    errors: List[Tuple[Any, str]] = []
    for item in targets:
        try:
            p = Path(item)
            if p.is_dir():
                new = p.with_name(f"_OLD_{stamp}_{p.name}")
                if new.exists():
                    continue
                p.rename(new)
                renamed.append((p, new, "dir"))
            elif p.is_file():
                new = p.with_name(f"_OLD_{stamp}_{p.name}")
                if new.exists():
                    continue
                p.rename(new)
                renamed.append((p, new, "file"))
        except Exception as e:
            errors.append((str(item), str(e)))
    return renamed, errors


def _result(renamed, errors) -> Dict[str, Any]:
    return {
        "ok": len(errors) == 0,
        "renamed": len(renamed),
        "errors": [{"target": t, "error": e} for (t, e) in errors],
        "items": [str(new) for (_old, new, _kind) in renamed],
    }


def _db_path() -> Path:
    # 書き込み側 Database が 実際 に 開く DB を 対象 に する (settings の 1 箇所 を 正)。
    # Target the DB actually opened by the writer (settings is the single truth).
    try:
        from pixel_sync.core import settings as _s
        return Path(str(_s.resolve_db_file()))
    except Exception:
        return _resolve(Path("database/nas2gp.db"))


def reset_progress() -> Dict[str, Any]:
    """進捗リセット: DB 本体 + WAL/SHM/journal + 旧バックアップ DB (最大 50 件) を OLD 退避。

    Progress reset: move the DB, WAL/SHM/journal files, and old backup DBs (up to 50) to OLD.
    """
    db_path = _db_path()
    db_dir = db_path.parent
    targets: List[Path] = [db_path]
    for suf in ("-wal", "-shm", "-journal"):
        extra = Path(str(db_path) + suf)
        if extra.exists():
            targets.append(extra)
    if db_dir and str(db_dir) not in ("", "."):
        if db_dir.exists():
            targets.extend(sorted(db_dir.glob("pixel_sync_backup_*.db"))[:50])
    renamed, errs = _rename_old(targets)
    return _result(renamed, errs)


def reset_settings() -> Dict[str, Any]:
    """設定リセット: config_ui.json を OLD 退避。

    Settings reset: move config_ui.json to OLD.
    """
    targets = [_resolve(_CONFIG_FILE)]
    renamed, errs = _rename_old(targets)
    return _result(renamed, errs)


def factory_reset() -> Dict[str, Any]:
    """全部やりなおし: DB フォルダ内 全ファイル + 設定 + 過去ログ (*.log) を OLD 退避。

    Factory reset: move all files in the DB folder plus settings and past logs (*.log) to OLD.
    """
    work_root = _work_root()
    db_path = _db_path()
    db_dir = db_path.parent if db_path.parent and str(db_path.parent) not in ("", ".") else _resolve(Path("database"))
    targets: List[Path] = []
    try:
        if db_dir.exists():
            for child in db_dir.iterdir():
                if child.is_file():
                    targets.append(child)
    except Exception:
        pass
    if db_path.exists():
        targets.append(db_path)
    for suf in ("-wal", "-shm", "-journal"):
        extra = Path(str(db_path) + suf)
        if extra.exists():
            targets.append(extra)
    targets.append(_resolve(_CONFIG_FILE))
    try:
        from pixel_sync.core.logger import Logger
        logs_dir = Path(getattr(Logger, "LOG_DIR", Path("logs")))
        if not logs_dir.is_absolute():
            logs_dir = (work_root / logs_dir).resolve()
        if logs_dir.exists():
            targets.extend(sorted(logs_dir.glob("*.log")))
    except Exception:
        pass

    uniq: List[Path] = []
    seen = set()
    for t in targets:
        try:
            s = str(Path(t).resolve())
        except Exception:
            s = str(t)
        if s in seen:
            continue
        seen.add(s)
        uniq.append(t)
    renamed, errs = _rename_old(uniq)
    return _result(renamed, errs)
