# ============================================
# FILE: pixel_sync/core/diagnostics.py
# VERSION: 1.0.0 (v1.7.15: Web 診断 ページ 用)
# VERSION: 1.0.0 (v1.7.15: for the Web diagnostics page)
# PURPOSE: GUI 診断 タブ と 同等 の 項目 を Qt 非依存 で 収集 する。
# PURPOSE: Collect the same items as the GUI diagnostics tab without Qt.
#   Web UI (/api/diagnostics) と バグレポート ZIP 生成 の 共通 ロジック。
#   Shared logic for the Web UI (/api/diagnostics) and bug report ZIP generation.
# ============================================
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pixel_sync.core.i18n import tr

# 診断 詳細 テーブル の 行 (GUI _ROW_KEYS と 同一 順序)
# Rows of the diagnostics detail table (same order as GUI _ROW_KEYS)
ROW_KEYS: Tuple[str, ...] = (
    "diag.row.adb_bin",
    "diag.row.adb_version",
    "diag.row.device_serial",
    "diag.row.device_model",
    "diag.row.device_android",
    "diag.row.device_storage",
    "diag.row.db_file",
    "diag.row.photo_dir",
    "diag.row.config_json",
    "diag.row.runtime_started",
    "diag.row.lang.code",
    "diag.row.lang.missing_keys",
    "diag.row.lang.missing_samples",
    "diag.row.lang.external_path",
)

_CONFIG_DIR = Path("config")
_CONFIG_FILE = _CONFIG_DIR / "config_ui.json"

# Web サーバ 起動 時刻 (create_app から set_runtime_started で 設定)
# Web server start time (set via set_runtime_started from create_app)
_RUNTIME_STARTED = ""


def set_runtime_started(text: str) -> None:
    global _RUNTIME_STARTED
    _RUNTIME_STARTED = str(text or "")


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


def _na() -> str:
    return tr("gui.remaining.na")


def _typo(key: str) -> str:
    """i18n キー を 日本語 ラベル へ。 未定義 キー は そのまま 返す。

    Convert an i18n key to its Japanese label; return unknown keys as-is.
    """
    try:
        t = tr(key)
        return str(t if t else key)
    except Exception:
        return key


def collect_diagnostics() -> Dict[str, Any]:
    """GUI 診断 タブ と 同等 の 診断 結果 を dict で 返す。

    Return diagnostics equivalent to the GUI diagnostics tab as a dict.
    """
    from pixel_sync.core import settings

    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows: Dict[str, Dict[str, Any]] = {
        k: {"key": k, "item": _typo(k), "value": "", "status": "na", "checked": ""}
        for k in ROW_KEYS
    }

    def set_row(key: str, value: Any, status: str = "na", checked: str = ""):
        r = rows.get(key)
        if r is None:
            return
        r["value"] = str(value if value is not None else "")
        r["status"] = str(status or "na")
        r["checked"] = str(checked or "")

    # ---------------- ADB ----------------
    adb_path = None
    adb_version = None
    try:
        from pixel_sync.device.adb import ADB
        adb_path = ADB.resolve_adb_path()
        adb_version = ADB.get_adb_version()
    except Exception:
        pass

    if adb_path:
        set_row("diag.row.adb_bin", adb_path, "ok", now_text)
        adb_status, adb_status_key = "ok", "diag.card.adb.ok"
    else:
        set_row("diag.row.adb_bin", _na(), "err", now_text)
        adb_status, adb_status_key = "err", "diag.card.adb.err"
    if adb_version:
        set_row("diag.row.adb_version", adb_version, "ok", now_text)
    else:
        set_row("diag.row.adb_version", _na(), "warn", now_text)
        if adb_status == "ok":
            adb_status, adb_status_key = "warn", "diag.card.adb.warn"

    # ---------------- デバイス ----------------
    # ---------------- Device ----------------
    device_info = None
    try:
        from pixel_sync.device.adb import ADB
        device_info = ADB.get_device_info()
    except Exception:
        device_info = None
    di = device_info or {}
    state = di.get("state") or "disconnected"
    serial = di.get("serial")
    model = di.get("model")
    manufacturer = di.get("manufacturer")
    android_version = di.get("android_version")
    build = di.get("build")
    storage_total = di.get("storage_total_gb")
    storage_free = di.get("storage_free_gb")
    checked_at = di.get("checked_at") or now_text

    set_row("diag.row.device_serial", serial or _na(),
            "ok" if serial else "err", checked_at)
    model_text = " ".join([x for x in [manufacturer, model] if x]) or _na()
    set_row("diag.row.device_model", model_text,
            "ok" if model else ("warn" if state == "connected" else "err"), checked_at)
    android_text = " / ".join([x for x in [
        ("Android " + str(android_version)) if android_version else None,
        ("Build " + str(build)) if build else None,
    ] if x]) or _na()
    set_row("diag.row.device_android", android_text,
            "ok" if android_version else ("warn" if state == "connected" else "err"), checked_at)
    if isinstance(storage_total, (int, float)) and isinstance(storage_free, (int, float)):
        set_row("diag.row.device_storage", f"{storage_free:.1f}GB / {storage_total:.1f}GB", "ok", checked_at)
    else:
        set_row("diag.row.device_storage", _na(),
                "warn" if state == "connected" else "err", checked_at)

    # ---------------- DB / PHOTO_DIR / config ----------------
    # 診断 の DB 表示 は 書き込み側 が 実際 に 開く パス (settings の 1 箇所 を 正) を 使う。
    # The diagnostics DB row shows the path actually opened by the writer
    # (settings is the single source of truth).
    try:
        db_file = str(settings.resolve_db_file())
    except Exception:
        db_file = getattr(settings, "DB_FILE", None)
    try:
        db_path: Optional[Path] = _resolve(db_file) if db_file else None
        ok = False
        note = ""
        if db_path and db_path.exists():
            ok, note = True, tr("diag.sub.db.exists")
        elif db_path is not None:
            parent = db_path.parent
            try:
                if not parent.exists():
                    parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            if parent.exists():
                ok, note = True, tr("diag.sub.db.not_yet_created")
        if ok:
            set_row("diag.row.db_file", f"{db_path}  ({note})", "ok", now_text)
        else:
            set_row("diag.row.db_file", str(db_path or _na()), "err", now_text)
    except Exception:
        set_row("diag.row.db_file", str(db_file or _na()), "err", now_text)

    photo_dir = getattr(settings, "PHOTO_DIR", None)
    try:
        pd_path = _resolve(photo_dir) if photo_dir else None
        if pd_path and pd_path.exists():
            set_row("diag.row.photo_dir", str(pd_path), "ok", now_text)
        else:
            set_row("diag.row.photo_dir", str(pd_path or _na()), "err", now_text)
    except Exception:
        set_row("diag.row.photo_dir", str(photo_dir or _na()), "err", now_text)

    config_path = _resolve(_CONFIG_FILE)
    try:
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            set_row("diag.row.config_json", str(config_path),
                    "ok" if isinstance(data, dict) else "warn", now_text)
        else:
            set_row("diag.row.config_json", str(config_path), "warn", now_text)
    except Exception:
        set_row("diag.row.config_json", str(config_path), "err", now_text)

    started_text = _RUNTIME_STARTED or now_text
    set_row("diag.row.runtime_started", f"NAS2GP Web - {started_text}", "ok", now_text)

    # ---------------- 言語リソース ----------------
    # ---------------- Language resources ----------------
    lang_card = {"level": "err", "status": _typo("diag.card.lang.subtitle_ng"), "detail": ""}
    try:
        from pixel_sync.core.i18n import LANGUAGE_EFFECTIVE, diagnose_wordbank
        eff_code = str(LANGUAGE_EFFECTIVE)
        d = diagnose_wordbank(eff_code)
        missing_count = int(d.get("missing_count", 0))
        ja_en_match = bool(d.get("ja_en_match"))
        level = "ok" if (missing_count == 0 and ja_en_match) else "warn"
        lang_card = {
            "level": level,
            "status": _typo("diag.card.lang.subtitle_ok" if level == "ok" else "diag.card.lang.subtitle_ng"),
            "detail": f"code={d.get('code', '')} / miss={missing_count}",
        }
        set_row("diag.row.lang.code", d.get("code", ""), level, now_text)
        set_row("diag.row.lang.missing_keys", missing_count,
                "ok" if missing_count == 0 else "warn", now_text)
        samples = d.get("missing_samples", []) or []
        set_row("diag.row.lang.missing_samples", ", ".join(samples[:10]),
                "ok" if not samples else "warn", now_text)
        ext_path = d.get("external_path")
        set_row("diag.row.lang.external_path", ext_path or _na(),
                "ok" if ext_path else "na", now_text)
    except Exception as e:
        lang_card["detail"] = f"{type(e).__name__}: {e}"

    cards = [
        {
            "key": "adb",
            "title": _typo("diag.card.adb.title"),
            "level": adb_status,
            "status": _typo(adb_status_key),
            "detail": " / ".join([x for x in [str(adb_path or ""), str(adb_version or "")] if x]),
        },
        {
            "key": "device",
            "title": _typo("diag.card.device.title"),
            "level": "ok" if state == "connected" else "err",
            "status": _typo("diag.card.device.ok" if state == "connected" else "diag.card.device.err"),
            "detail": " / ".join([x for x in [
                model_text if model_text != _na() else "",
                str(serial or ""),
                ("Android " + str(android_version)) if android_version else "",
            ] if x]) or _na(),
        },
        {
            "key": "lang",
            "title": _typo("diag.card.lang.title"),
            "level": lang_card["level"],
            "status": lang_card["status"],
            "detail": lang_card["detail"],
        },
    ]

    return {
        "ok": True,
        "checked_at": now_text,
        "cards": cards,
        "rows": [rows[k] for k in ROW_KEYS],
    }


def build_bug_report_zip() -> Tuple[bytes, str]:
    """ログ 3 件 + config + DB + 診断 結果 を まとめ た ZIP を メモリ 上 で 生成 する。
    (GUI 版 の バグレポート から スクリーンショット を 除い た Web 版)

    Build in memory a ZIP containing the latest 3 logs + config + DB + diagnostics.
    (Web version of the GUI bug report, without screenshots.)
    """
    from pixel_sync.core.logger import Logger

    work_root = _work_root()
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ログ (最新 3 件)
        # Logs (latest 3)
        try:
            logs_dir = Path(getattr(Logger, "LOG_DIR", work_root / "logs"))
            if not logs_dir.is_absolute():
                logs_dir = (work_root / logs_dir).resolve()
            if logs_dir.exists():
                candidates = sorted(
                    [p for p in logs_dir.iterdir() if p.is_file() and p.suffix == ".log"],
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )[:3]
                for src in candidates:
                    try:
                        zf.writestr(f"logs/{src.name}", src.read_bytes())
                    except Exception:
                        pass
        except Exception:
            pass

        # config / DB
        for rel in (_CONFIG_FILE,):
            src = _resolve(rel)
            if src.exists() and src.is_file():
                try:
                    zf.writestr(str(rel).replace("\\", "/"), src.read_bytes())
                except Exception:
                    pass
        try:
            from pixel_sync.core import settings as _s
            # 実 DB パス (書き込み側 が 開く ファイル) を 添付 する。
            # Attach the actual DB file opened by the writer.
            db_raw = str(_s.resolve_db_file())
            if db_raw:
                db_path = _resolve(db_raw)
                if db_path.exists() and db_path.is_file():
                    zf.writestr(f"database/{db_path.name}", db_path.read_bytes())
        except Exception:
            pass

        # 診断 結果 (TSV)
        # Diagnostics result (TSV)
        try:
            lines = ["item\tvalue\tstatus\tchecked"]
            for r in collect_diagnostics().get("rows", []):
                lines.append("\t".join([
                    str(r.get("item", "")), str(r.get("value", "")),
                    str(r.get("status", "")), str(r.get("checked", "")),
                ]))
            zf.writestr("diagnostics.txt", "\n".join(lines).encode("utf-8"))
        except Exception:
            pass

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return buf.getvalue(), f"NAS2GP_bugreport_{ts}.zip"
