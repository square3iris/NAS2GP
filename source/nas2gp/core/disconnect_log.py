# ============================================
# FILE: nas2gp/core/disconnect_log.py
#
# 切断解析ログ (Disconnect analysis log)
#   A. 切断スナップショット: 切断 (復旧不能) を 検知 し た とき
#      logs/disconnect_YYYYMMDD_HHMMSS.log を 1 件 出力 する 。
#   B. 定期ハートビート: DISCONNECT_HEARTBEAT_INTERVAL_SEC ごとに 端末 状態 を
#      logs/health.jsonl へ 1 行 追記 し 、 コンソール に も 1 行 出す 。
#
#   phase / batch / 直近ファイル は events 購読 で 自動 追跡 する ため 、
#   転送 ループ へ の 追加 負荷 は ない (オンデマンド 設計)。
#
#   A. Disconnect snapshot: writes logs/disconnect_YYYYMMDD_HHMMSS.log on an
#      unrecoverable disconnect.
#   B. Periodic heartbeat: appends one device-state line to logs/health.jsonl
#      every DISCONNECT_HEARTBEAT_INTERVAL_SEC and echoes one console line.
#
#   phase / batch / last file are tracked automatically via event subscription,
#   so there is no extra load on the transfer loop (on-demand design).
# ============================================

from __future__ import annotations

import json
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from nas2gp.core.i18n import tr

_LOCK = threading.Lock()
_STOP_EVENT = threading.Event()
_THREAD: Optional[threading.Thread] = None
_SUBSCRIBED = False

# 現在 の 実行 コンテキスト (events 購読 で 更新)
# Current run context (updated via event subscription).
_CTX = {"phase": "", "batch": None, "last_file": ""}

# 直近 の ハートビート 記録 (スナップショット の 残量推移 用)
# Recent heartbeat records (for the snapshot's battery trend).
_RECENT: List[dict] = []
_RECENT_MAX = 12


# ------------------------------------------------------------------
# 設定 / パス (動的 参照 = リロード 反映)
# Settings / paths (dynamic lookup so reloads apply)
# ------------------------------------------------------------------

def _logs_dir() -> Path:
    try:
        from nas2gp.core import settings as _s
        return Path(str(_s.WORK_ROOT)) / "logs"
    except Exception:
        return Path("logs")


def _enabled() -> bool:
    try:
        from nas2gp.core import settings as _s
        return bool(getattr(_s, "DISCONNECT_LOG_ENABLED", True))
    except Exception:
        return True


def _interval() -> int:
    try:
        from nas2gp.core import settings as _s
        v = int(getattr(_s, "DISCONNECT_HEARTBEAT_INTERVAL_SEC", 600) or 600)
    except Exception:
        v = 600
    return max(30, v)


# ------------------------------------------------------------------
# 端末 情報 取得
# Device probes
# ------------------------------------------------------------------

def _health() -> dict:
    try:
        from nas2gp.device.adb import ADB
        return ADB.get_device_health() or {}
    except Exception:
        return {}


def _adb_raw(*args) -> str:
    """adb コマンド の 生 出力 (stdout 優先、 無ければ stderr) を 文字列 で 返す 。
    Return the raw adb output (stdout, else stderr) as a string."""
    try:
        r = subprocess.run(
            ["adb", *[str(a) for a in args]],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        return out or err or "(no output)"
    except Exception as e:
        return f"(error: {type(e).__name__})"


def _fmt(v, dash: str = "?") -> str:
    return dash if v is None else str(v)


# ------------------------------------------------------------------
# コンテキスト 追跡 (events 購読)
# Context tracking (event subscription)
# ------------------------------------------------------------------

def _on_phase(name, data):
    d = data if isinstance(data, dict) else {}
    with _LOCK:
        if d.get("phase"):
            _CTX["phase"] = str(d["phase"])
        elif name:
            _CTX["phase"] = str(name)
        if d.get("batch_idx") is not None:
            try:
                _CTX["batch"] = int(d["batch_idx"])
            except Exception:
                pass


def _on_progress(name, data):
    d = data if isinstance(data, dict) else {}
    with _LOCK:
        if d.get("batch_idx") is not None:
            try:
                _CTX["batch"] = int(d["batch_idx"])
            except Exception:
                pass
        if d.get("current_file"):
            _CTX["last_file"] = str(d["current_file"])


def _on_unrecoverable(name, data):
    """ADB_DEVICE_UNRECOVERABLE を 受信 し たら 解析 スナップショット を 出力 する 。
    復旧ゲート (phase == "recovery_gate") の 結論 の とき だけ 出力 し 、 各 バッチ 冒頭 の
    在否チェック 由来 の 反復 publish で スナップショット が 乱発 さ れる の を 防ぐ 。

    Write the analysis snapshot when ADB_DEVICE_UNRECOVERABLE is received. It only fires for
    the recovery gate's conclusion (phase == "recovery_gate") so that repeated publishes from
    the per-batch presence check do not spam snapshots."""
    d = data if isinstance(data, dict) else {}
    if str(d.get("phase") or "") != "recovery_gate":
        return
    reason = str(d.get("detail") or name)
    extra = [f"trigger phase: {d.get('phase')}"]
    write_snapshot(reason=reason, extra_lines=extra)


def _subscribe():
    global _SUBSCRIBED
    if _SUBSCRIBED:
        return
    try:
        from nas2gp.core import events as ev
        ev.subscribe(ev.SYNC_PHASE_CHANGED, _on_phase)
        ev.subscribe(ev.UPLOAD_PROGRESS_TICK, _on_progress)
        ev.subscribe(ev.ADB_DEVICE_UNRECOVERABLE, _on_unrecoverable)
        _SUBSCRIBED = True
    except Exception:
        pass


# ------------------------------------------------------------------
# B. ハートビート
# B. Heartbeat
# ------------------------------------------------------------------

def _record_heartbeat() -> None:
    h = _health()
    with _LOCK:
        ctx = dict(_CTX)
    rec = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "phase": ctx.get("phase") or "",
        "batch": ctx.get("batch"),
        "last_file": ctx.get("last_file") or "",
        "ok": bool(h.get("ok")),
        "level": h.get("battery_level"),
        "charging": h.get("battery_charging"),
        "temp_c": h.get("battery_temp_c"),
        "free_gb": h.get("storage_free_gb"),
    }
    with _LOCK:
        _RECENT.append(rec)
        if len(_RECENT) > _RECENT_MAX:
            del _RECENT[0]
    try:
        p = _logs_dir() / "health.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # コンソール に 1 行 (日本語 整形 サマリ / Web コンソール へ も 流れる)
    # One console line (formatted summary; also captured by the Web console).
    try:
        from nas2gp.core.logger import Logger
        if h.get("ok"):
            Logger.info(tr("log.disconnect.heartbeat").format(
                level=_fmt(h.get("battery_level")),
                chg=tr("log.disconnect.charging_yes") if h.get("battery_charging")
                else tr("log.disconnect.charging_no"),
                temp=_fmt(h.get("battery_temp_c")),
                free=_fmt(h.get("storage_free_gb")),
                batch=_fmt(ctx.get("batch"), dash="-"),
            ))
        else:
            Logger.warning(tr("log.disconnect.heartbeat_ng").format(
                batch=_fmt(ctx.get("batch"), dash="-")))
    except Exception:
        pass


def _heartbeat_loop():
    while not _STOP_EVENT.is_set():
        try:
            _record_heartbeat()
        except Exception:
            pass
        # 停止 応答 を 速く する ため wait() で 待つ 。
        # Wait via wait() so stop() is responsive.
        _STOP_EVENT.wait(max(1.0, float(_interval())))


# ------------------------------------------------------------------
# A. 切断 スナップショット
# A. Disconnect snapshot
# ------------------------------------------------------------------

def write_snapshot(reason: str = "", extra_lines: Optional[List[str]] = None) -> Optional[str]:
    """切断 解析 スナップショット を logs/disconnect_*.log へ 出力 し 、 パス を 返す 。
    無効 設定 / 失敗 時 は None を 返す (本体 処理 を 止め ない)。

    Write the disconnect analysis snapshot to logs/disconnect_*.log and return its
    path. Returns None when disabled or on failure (never interrupts the main flow)."""
    if not _enabled():
        return None
    now = datetime.now()
    with _LOCK:
        ctx = dict(_CTX)
        recent = list(_RECENT)
    h = _health()

    L: List[str] = []
    L.append(tr("log.disconnect.snap_title"))
    L.append(tr("log.disconnect.snap_time").format(ts=now.strftime("%Y-%m-%d %H:%M:%S")))
    L.append(tr("log.disconnect.snap_reason").format(reason=(reason or "-")))
    L.append(tr("log.disconnect.snap_ctx").format(
        phase=(ctx.get("phase") or "-"),
        batch=_fmt(ctx.get("batch"), dash="-"),
        file=(ctx.get("last_file") or "-"),
    ))
    L.append("")
    L.append(tr("log.disconnect.snap_health_hdr"))
    if h.get("ok"):
        L.append(tr("log.disconnect.snap_health").format(
            level=_fmt(h.get("battery_level")),
            chg=tr("log.disconnect.charging_yes") if h.get("battery_charging")
            else tr("log.disconnect.charging_no"),
            temp=_fmt(h.get("battery_temp_c")),
            free=_fmt(h.get("storage_free_gb")),
        ))
    else:
        L.append(tr("log.disconnect.snap_health_ng"))
    L.append("")
    L.append(tr("log.disconnect.snap_recent_hdr"))
    if recent:
        for r in recent:
            L.append(tr("log.disconnect.snap_recent_row").format(
                ts=r.get("ts") or "-",
                level=_fmt(r.get("level")),
                chg=("true" if r.get("charging") else "false") if r.get("charging") is not None else "?",
                temp=_fmt(r.get("temp_c")),
            ))
    else:
        L.append("(none)")
    L.append("")
    L.append(tr("log.disconnect.snap_adb_hdr"))
    L.append(f"$ adb devices -l\n{_adb_raw('devices', '-l')}")
    L.append(f"$ adb get-state\n{_adb_raw('get-state')}")
    if extra_lines:
        L.append("")
        L.append(tr("log.disconnect.snap_extra_hdr"))
        for ln in extra_lines:
            L.append(str(ln))
    text = "\n".join(L) + "\n"

    try:
        d = _logs_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"disconnect_{now:%Y%m%d_%H%M%S}.log"
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            from nas2gp.core.logger import Logger
            Logger.warning(tr("log.disconnect.snap_saved").format(path=p.name))
        except Exception:
            pass
        return str(p)
    except Exception:
        return None


# ------------------------------------------------------------------
# 開始 / 停止 (同期 実行 の try/finally から 呼ぶ)
# Start / stop (called from the sync run's try/finally)
# ------------------------------------------------------------------

def start() -> bool:
    """ハートビート を 開始 する (有効 設定 の ときのみ)。 events 購読 も 行う 。
    Start the heartbeat (only when enabled); also installs the event subscription."""
    global _THREAD
    _subscribe()
    if not _enabled():
        return False
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return True
    _STOP_EVENT.clear()
    t = threading.Thread(target=_heartbeat_loop, name="DisconnectHeartbeat", daemon=True)
    _THREAD = t
    t.start()
    return True


def stop() -> None:
    """ハートビート を 停止 する (購読 は 維持 = 再開 を 軽く する)。
    Stop the heartbeat (the subscription is kept so restarts stay cheap)."""
    global _THREAD
    _STOP_EVENT.set()
    t = _THREAD
    _THREAD = None
    try:
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
    except Exception:
        pass
