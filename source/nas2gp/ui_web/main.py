import os
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

import re as _re


# Logger._write が 自 ら print() する 行 ([ts] LEVEL msg) を 検出 し 、
# stdout tee 側 で は スキップ し て 二重 登録 を 防ぐ 。
# Detects lines that Logger._write already prints ([ts] LEVEL msg) so the stdout
# tee skips them and avoids duplicate log entries.
_LOG_LINE_RE = _re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] [A-Z]+ ")


class _WebTee:
    """v1.7.15: sys.stdout / sys.stderr を 横取り し 、 print() や print_phase() の
    出力 を Web コンソール 表示 用 バッファ へ 流す (Qt 非依存)。

    v1.7.15: Intercepts sys.stdout / sys.stderr and routes print() / print_phase()
    output into the Web console buffer (Qt-independent)."""

    def __init__(self, original, sink, level="PRINT"):
        self._orig = original
        self._sink = sink
        self._level = str(level or "PRINT")
        self._buf = ""
        self._lock = threading.Lock()
        self.encoding = getattr(original, "encoding", "utf-8")
        self.errors = getattr(original, "errors", "replace")
        self.name = getattr(original, "name", f"<webtee:{self._level}>")

    def write(self, s):
        if s is None:
            return 0
        text = str(s)
        try:
            with self._lock:
                self._orig.write(text)
                try:
                    self._orig.flush()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            with self._lock:
                self._buf += text
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    line = line.replace("\r", "").rstrip()
                    if not line or _LOG_LINE_RE.match(line):
                        continue
                    self._sink(self._level, line)
        except Exception:
            pass
        return len(text)

    def flush(self):
        try:
            with self._lock:
                self._orig.flush()
        except Exception:
            pass
        try:
            with self._lock:
                if self._buf:
                    line = self._buf.replace("\r", "").rstrip()
                    self._buf = ""
                    if line and not _LOG_LINE_RE.match(line):
                        self._sink(self._level, line)
        except Exception:
            pass

    def writelines(self, lines):
        for l in (lines or []):
            self.write(l)

    def isatty(self):
        return False

    def fileno(self):
        try:
            return int(self._orig.fileno())
        except Exception:
            return -1


class WebSyncRuntime:
    """
    threading.Thread ベース の 同期 ワーカー。
    SyncFlow の ロジック は 共通 (nas2gp.worker.sync_flow)。

    Thread-based sync worker; the SyncFlow logic is shared (nas2gp.worker.sync_flow).
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.state: Dict[str, Any] = {
            "running": False,
            "phase": "IDLE",
            "percent": 0,
            "batch_idx": 0,
            "total_batches": 0,
            "remaining_files": 0,
            "total_files": 0,
            "speed_bps": 0,
            "eta_seconds": 0,
            "last_file": "",
            "last_event": "",
            "phase_detail": "",
            "phase_title": "",
            "phase_no": None,
            "phase_total": None,
            "phase_batch": None,
            "started_at": None,
            "finished_at": None,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "pending_bytes": 0,
            "processed_bytes": 0,
            "gp_items": None,
            "gp_eta_seconds": None,
            "gp_eta_text": "",
            "gp_detail": "",
            "gp_complete": False,
            # v1.7.15: 「今回のバッチで終了」 要求 が 受付 済み か (画面 表示 用)
            # v1.7.15: Whether a "stop after batch" request is accepted (for display).
            "stop_after_batch": False,
            # v1.7.18: ADB 復旧不能 (切断 で 安全 停止) を 通知 する 永続 アラート 文言。
            # v1.7.18: Persistent alert text for an unrecoverable ADB state (safe stop).
            "adb_alert": "",
        }
        self.logs: List[Dict[str, Any]] = []
        self.config: Optional[Dict[str, Any]] = None
        # v1.13.9: 前回 の 途中 停止 チェックポイント から 再開 する か (開始 時 に UI が 指定)。
        # v1.13.9: Whether to resume from the previous interrupted-run checkpoint (set by the UI on start).
        self.resume_mode = False
        self._events_installed = False
        self._tee_installed = False
        self._saved_stdout = None
        self._saved_stderr = None

    def _append_log(self, level, message, ts=None):
        """v1.7.15: Web コンソール 表示 用 に 1 行 追記 する。
        Logger フック (_install_web_capture) と stdout tee (_WebTee) の 共通 入口。

        Appends one line for the Web console; shared entry for the Logger hook
        and the stdout tee."""
        try:
            msg = str(message if message is not None else "").rstrip()
            if not msg:
                return
            row = {
                "t": (ts or time.strftime("%Y-%m-%d %H:%M:%S")),
                "lv": str(level or "INFO"),
                "msg": msg[:600],
            }
            with self.lock:
                self.logs.append(row)
                try:
                    from nas2gp.core import settings as _ws
                    cap = int(getattr(_ws, "WEB_MAX_LOG_LINES", 120) or 120)
                except Exception:
                    cap = 120
                cap = max(cap, 500)
                while len(self.logs) > cap + 20:
                    del self.logs[0]
        except Exception:
            pass

    def _install_stdout_capture(self):
        """v1.7.15: print() / print_phase() など の 標準出力 を Web コンソール へ 取り込む。
        Cmd プロンプト と 同じ 内容 を 画面 でも 見られる よう に する。

        Captures stdout (print / print_phase) into the Web console so the same
        content as the terminal is visible in the browser."""
        if self._tee_installed:
            return
        try:
            import sys as _sys

            def _sink(level, line):
                self._append_log(level, line)

            self._saved_stdout = _sys.stdout
            self._saved_stderr = _sys.stderr
            _sys.stdout = _WebTee(self._saved_stdout, _sink, "PRINT")
            _sys.stderr = _WebTee(self._saved_stderr, _sink, "ERROR")
            self._tee_installed = True
        except Exception:
            pass

    def _install_event_subscribers(self):
        if self._events_installed:
            return
        try:
            from nas2gp.core import events as ev_mod
            from nas2gp.core.logger import Logger

            def _on_upload_progress(name, data):
                if not isinstance(data, dict):
                    return
                with self.lock:
                    self.state["phase"] = "UPLOAD"
                    # v1.7.15: tick に 載っ て い る キー だけ を 更新 する。
                    #   (ファイル毎 tick は remain_files 等 を 含ま な い ため 、
                    #    0 で 上書き する と 「残りファイル」 が 実行中 に 0 に な る 不具合 を 防ぐ)
                    # v1.7.15: Update only the keys present in the tick (per-file ticks
                    #   lack remain_files etc.; overwriting with 0 would wrongly zero it).
                    if "batch_idx" in data:
                        self.state["batch_idx"] = int(data.get("batch_idx") or 0)
                    if "total_batches" in data:
                        self.state["total_batches"] = int(data.get("total_batches") or 0)
                    if "remain_files" in data:
                        self.state["remaining_files"] = int(data.get("remain_files") or 0)
                    if "speed_bps" in data:
                        self.state["speed_bps"] = int(data.get("speed_bps") or 0)
                    if "eta_seconds" in data:
                        self.state["eta_seconds"] = int(data.get("eta_seconds") or 0)
                    if data.get("current_file"):
                        self.state["last_file"] = str(data.get("current_file"))
                    # v1.7.15: 成功/失敗/スキップ の 累計 が 載っ て いれ ば 逐次 反映
                    #   (実行 中 も 0/0/0 の まま に な ら な い よう に する)。
                    # v1.7.15: Reflect success/failed/skipped totals as they arrive.
                    for _cnt_key in ("success", "skipped", "failed"):
                        if _cnt_key in data:
                            self.state[_cnt_key] = int(data.get(_cnt_key) or 0)
                    tot = int(self.state.get("total_files") or 0)
                    rem = int(self.state.get("remaining_files") or 0)
                    if tot > 0:
                        self.state["percent"] = round(max(0.0, min(100.0, ((tot - rem) / tot) * 100.0)), 2)

            def _on_phase(name, data):
                d = data if isinstance(data, dict) else {}
                with self.lock:
                    if d.get("phase"):
                        self.state["phase"] = str(d["phase"])
                    elif name and name != ev_mod.SYNC_PHASE_CHANGED and "_" in name:
                        # v1.14.0: SYNC_PHASE_CHANGED は phase を 欠く こと が ある
                        #   (print_phase の 見出し 通知)。 その 場合 は 前回 の フェーズ を
                        #   保持 する 。 以前 は イベント 名 から "PHASE_CHANGED" を 作り 、
                        #   長時間 の バックアップ 待機 中 に 表示 が 汎用 文言 へ 落ち て いた 。
                        # v1.14.0: SYNC_PHASE_CHANGED may lack a phase (print_phase
                        #   headings); keep the previous phase then. It used to derive
                        #   "PHASE_CHANGED" from the event name, degrading the display to
                        #   a generic label during long backup waits.
                        self.state["phase"] = str(name.split("_", 1)[1])[:20]
                    self.state["last_event"] = str(name)
                    if d.get("detail"):
                        self.state["phase_detail"] = str(d["detail"])
                    # v1.7.15: コンソール 見出し と 同じ フェーズ 文言 を 取り込む
                    # v1.7.15: Capture the same phase title as the console heading.
                    if d.get("phase_title"):
                        self.state["phase_title"] = str(d["phase_title"])
                    if d.get("phase_no") is not None:
                        self.state["phase_no"] = int(d["phase_no"])
                        self.state["phase_total"] = (
                            int(d["phase_total"]) if d.get("phase_total") is not None else None
                        )
                    # v1.7.16: 工程 インジケータ 用 に 対象 バッチ 番号 を 保持 (バッチ 切替 時 の リセット 判定)
                    # v1.7.16: Keep the target batch index for the phase indicator
                    # (used to detect batch switches and reset).
                    if d.get("batch_idx") is not None:
                        self.state["phase_batch"] = int(d["batch_idx"])

            def _on_log_capture(level: str, message: str, ts=None, **kwargs):
                self._append_log(level, message, ts)

            def _on_backup_rules(name, data):
                """v1.7.14: Google Photos の 「残り枚数 / 残り時間」 を 画面 表示 用 に 取り込む。

                Captures the remaining items / time for screen display."""
                d = data if isinstance(data, dict) else {}
                gp = d.get("gp") if isinstance(d.get("gp"), dict) else {}
                with self.lock:
                    self.state["gp_items"] = gp.get("items")
                    self.state["gp_eta_seconds"] = gp.get("eta_seconds")
                    self.state["gp_eta_text"] = str(gp.get("eta_text") or "")
                    self.state["gp_detail"] = str(gp.get("detail_text") or "")
                    self.state["gp_complete"] = bool(gp.get("complete"))

            def _on_adb_unrecoverable(name, data):
                """v1.7.18: ADB 復旧不能 を Web UI に 通知 する (実行 終了 後 も 残る 永続 アラート)。
                v1.7.18: Notify the Web UI of an unrecoverable ADB state
                (a persistent alert that remains after the run ends)."""
                d = data if isinstance(data, dict) else {}
                with self.lock:
                    self.state["adb_alert"] = str(d.get("detail") or name)

            def _on_adb_recovered(name, data):
                """v1.7.18: ADB 復旧 を 検知 し た ら 永続 アラート を 解除 する。
                v1.7.18: Clear the persistent alert once ADB recovery is detected."""
                with self.lock:
                    self.state["adb_alert"] = ""
                _on_phase(name, data)

            ev_mod.subscribe(ev_mod.UPLOAD_PROGRESS_TICK, _on_upload_progress)
            ev_mod.subscribe(ev_mod.SYNC_PHASE_CHANGED, _on_phase)
            try:
                ev_mod.subscribe(ev_mod.BACKUP_RULES_TICK, _on_backup_rules)
            except Exception:
                pass
            try:
                ev_mod.subscribe(ev_mod.ADB_DEVICE_OFFLINE_PERSISTENT, _on_phase)
                ev_mod.subscribe(ev_mod.ADB_DEVICE_UNAUTHORIZED, _on_phase)
                ev_mod.subscribe(ev_mod.ADB_DEVICE_RECOVERED, _on_adb_recovered)
                ev_mod.subscribe(ev_mod.ADB_DEVICE_UNRECOVERABLE, _on_adb_unrecoverable)
                ev_mod.subscribe(ev_mod.DEVICE_STORAGE_INSUFFICIENT, _on_phase)
                ev_mod.subscribe(ev_mod.DEVICE_STORAGE_RECOVERED, _on_phase)
            except Exception:
                pass
            try:
                Logger._install_web_capture(_on_log_capture)
            except Exception:
                pass
            # print() / print_phase() の 標準出力 も Web コンソール へ 取り込む
            # Also capture stdout (print / print_phase) into the Web console.
            self._install_stdout_capture()
            self._events_installed = True
        except Exception:
            pass

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return dict(self.state)

    def recent_logs(self, limit=120) -> List[Dict[str, Any]]:
        with self.lock:
            return list(self.logs[-limit:])

    def _run_sync(self, cfg: Dict[str, Any]):
        self._install_event_subscribers()
        try:
            with self.lock:
                self.state["running"] = True
                self.state["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self.state["finished_at"] = None
                self.state["success"] = 0
                self.state["failed"] = 0
                self.state["skipped"] = 0
                # 新規実行 時 は 前回 の Google Photos 残り情報 を クリア (古い値 の 残留 表示 を 防ぐ)
                # On a new run, clear the previous Google Photos progress (avoid stale values).
                self.state["gp_items"] = None
                self.state["gp_eta_seconds"] = None
                self.state["gp_eta_text"] = ""
                self.state["gp_detail"] = ""
                self.state["gp_complete"] = False
                # v1.7.18: 新規 実行 時 は 前回 の ADB 復旧不能 アラート を クリア
                # v1.7.18: Clear the previous ADB unrecoverable alert on a new run.
                self.state["adb_alert"] = ""
                # 新規実行 時 は 前回 の フェーズ 見出し も クリア
                # Also clear the previous phase heading on a new run.
                self.state["phase_title"] = ""
                self.state["phase_no"] = None
                self.state["phase_total"] = None
                self.state["phase_batch"] = None
                self.state["stop_after_batch"] = False
            # v1.7.15: 前回 の 停止 要求 を クリア
            # v1.7.15: Clear the previous stop request.
            try:
                from nas2gp.core import control as _control
                _control.reset()
            except Exception:
                pass

            from nas2gp.storage.db import Database
            from nas2gp.worker.sync_flow import Sync as _Sync
            from nas2gp.core.settings import PHOTO_DIR as _PHOTO_DIR_DEF
            import nas2gp.core.settings as _settings_mod

            # DB_FILE は settings.DB_FILE (resolve_db_file) を 唯一 の 正 と する。
            # DB_FILE: settings.DB_FILE (resolve_db_file) is the single source of truth.
            photo_cfg_val = str((cfg or {}).get("PHOTO_DIR") or _PHOTO_DIR_DEF)
            try:
                import pathlib as _pl
                photo_path = _pl.Path(photo_cfg_val)
            except Exception:
                photo_path = _PHOTO_DIR_DEF
            setattr(_settings_mod, "PHOTO_DIR", photo_path)

            db = Database()
            # v1.13.9: 途中 再開 を 選ば な かっ た 場合 は 、 前回 の チェックポイント を
            #   破棄 し て 最初 から 実行 する (UI の 「最初から」 選択 に 対応)。
            # v1.13.9: When not resuming, discard the previous checkpoint and start over
            #   (matches the UI's "start over" choice).
            if not bool(getattr(self, "resume_mode", False)):
                try:
                    db.clear_resume_checkpoint()
                except Exception:
                    pass
            sync = _Sync(db)
            # v1.13.9: 途中 再開 モード を Uploader へ 引き継ぐ 。
            # v1.13.9: Propagate the resume mode to the Uploader.
            try:
                sync.uploader.resume_mode = bool(getattr(self, "resume_mode", False))
            except Exception:
                pass
            # Sync.upload_all 内部 で events 経由 UPLOAD_PROGRESS_TICK / SYNC_PHASE_CHANGED 発行 さ れ て いる 想定
            # Assumes Sync.upload_all publishes UPLOAD_PROGRESS_TICK / SYNC_PHASE_CHANGED via events.
            # v1.7.18: 切断解析ログ (ハートビート) を 同期 実行 中 のみ 開始 する 。
            # v1.7.18: Start the disconnect-analysis heartbeat only during the sync run.
            _dlog = None
            try:
                from nas2gp.core import disconnect_log as _dlog
                _dlog.start()
            except Exception:
                _dlog = None

            result = None
            try:
                result = sync.upload_all()
            except Exception as ex:
                with self.lock:
                    self.state["phase"] = "ERROR"
                    self.state["last_file"] = f"{type(ex).__name__}: {str(ex)[:200]}"
                raise
            finally:
                # v1.7.18: 同期 終了 時 に ハートビート を 停止 する 。
                # v1.7.18: Stop the heartbeat when the sync run ends.
                try:
                    if _dlog is not None:
                        _dlog.stop()
                except Exception:
                    pass
                with self.lock:
                    self.state["running"] = False
                    self.state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    if isinstance(result, tuple) and len(result) >= 3:
                        self.state["success"] = int(result[0] or 0)
                        self.state["skipped"] = int(result[1] or 0)
                        self.state["failed"] = int(result[2] or 0)
                    elif isinstance(result, dict):
                        for k in ["success", "skipped", "failed"]:
                            if k in result:
                                self.state[k] = int(result[k] or 0)
                    if self.state.get("phase") not in {"ERROR"}:
                        self.state["phase"] = "DONE"
        except Exception:
            with self.lock:
                self.state["running"] = False
                self.state["finished_at"] = self.state["finished_at"] or time.strftime("%Y-%m-%d %H:%M:%S")
                if self.state.get("phase") != "ERROR":
                    self.state["phase"] = "ERROR"
            raise

    def start(self, cfg: Dict[str, Any], resume: bool = False):
        with self.lock:
            if self.state["running"]:
                return False
            self.config = dict(cfg or {})
            # v1.13.9: 途中 再開 の 可否 を スレッド 起動 前 に 確定 する 。
            # v1.13.9: Fix the resume choice before the worker thread starts.
            self.resume_mode = bool(resume)
            self.stop_event.clear()
            self.thread = threading.Thread(
                target=self._run_sync, args=(self.config,), name="WebSyncFlow", daemon=True
            )
            self.thread.start()
            return True

    def stop(self):
        """v1.7.15: 即時 停止 。 未送信 ファイル の 転送 を 中断 する
        (送信 済み 分 の GP バックアップ 待ち / 削除 は 安全 の ため 継続)。

        Immediate stop: aborts transfer of unsent files (waits/deletes for already
        sent files continue for safety)."""
        try:
            from nas2gp.core import control as _control
            _control.request_stop_now()
        except Exception:
            pass
        with self.lock:
            self.state["phase"] = "STOP_REQUESTED"

    def stop_after_batch(self):
        """v1.7.15: 「今回のバッチで終了」 。 実行 中 の バッチ を 完了 し て から 終了 する。

        v1.7.15: "Finish after this batch"; stops after the running batch completes."""
        try:
            from nas2gp.core import control as _control
            _control.request_stop_after_batch()
        except Exception:
            pass
        with self.lock:
            self.state["stop_after_batch"] = True


RUNTIME = WebSyncRuntime()


def _settings_ui_schema() -> List[Dict[str, Any]]:
    """Web 設定画面 の 表示 メタ情報 (GUI の SettingsTab 相当)。
    ラベル / ツールチップ は core.i18n の tr() を 再利用 し GUI と 文言 を 共通化 する。
    type: bool / int / float / gb / select / text
      - gb: 内部 は バイト 値 だが 画面 は GB 単位 で 編集 する (MAX_BYTES_PER_BATCH 等)

    Display metadata for the Web settings screen (equivalent to the GUI SettingsTab).
    Labels / tooltips reuse core.i18n tr(). type: bool / int / float / gb / select / text
    (gb: stored as bytes but edited in GB, e.g. MAX_BYTES_PER_BATCH).
    """
    from nas2gp.core.i18n import tr, list_available_languages

    lang_choices = []
    for code, raw in list_available_languages():
        label = str(raw)
        if label.startswith("external:"):
            label = label[len("external:"):]
        elif label.startswith("settings.lang."):
            label = tr(label)
        lang_choices.append({"value": str(code), "label": label})

    # v1.7.19: バッチ工程 (①容量確保 → ⑥GP待機 など) の順に並べ替え、各グループ/項目に
    # 短い説明 (desc) を付け、どの工程の設定か・変えると何が変わるかを画面上で分かるようにした。
    # v1.7.19: Reordered along the batch phases (①CAPACITY .. ⑥WAIT_BACKUP) and added a short
    # desc to every group/item so the screen shows which step each setting controls and its effect.
    return [
        {"title": tr("settings.group.basic"), "desc": tr("settings.group.basic.desc"), "items": [
            {"key": "LANGUAGE", "label": tr("settings.label.lang"), "type": "select",
             "choices": lang_choices, "desc": tr("settings.tooltip.lang")},
            {"key": "PHOTO_DIR", "label": tr("settings.label.photo_dir"), "type": "text",
             "desc": tr("settings.tooltip.photo_dir")},
            {"key": "DEVICE_WORK_DIR", "label": tr("settings.label.work_dir"), "type": "text",
             "desc": tr("settings.tooltip.work_dir")},
        ]},
        {"title": tr("settings.group.capacity"), "desc": tr("settings.group.capacity.desc"), "items": [
            {"key": "BATCH_STRATEGY", "label": tr("settings.label.batch_mode"), "type": "select",
             "choices": [
                 {"value": "count", "label": tr("settings.batch_mode.count")},
                 {"value": "bytes", "label": tr("settings.batch_mode.bytes")},
                 {"value": "hybrid", "label": tr("settings.batch_mode.hybrid")},
             ], "desc": tr("settings.tooltip.batch_mode")},
            {"key": "MAX_FILES_PER_BATCH", "label": tr("settings.label.batch_size"), "type": "int",
             "min": 1, "max": 2000, "desc": tr("settings.tooltip.batch_size")},
            {"key": "MAX_BYTES_PER_BATCH", "label": tr("settings.label.batch_size_gb"), "type": "gb",
             "min": 0, "max": 64, "step": 0.5, "desc": tr("settings.tooltip.batch_size_gb")},
            {"key": "DEVICE_STORAGE_MARGIN_GB", "label": tr("settings.label.storage_margin_gb"), "type": "gb",
             "min": 0, "max": 10, "step": 0.5, "desc": tr("settings.tooltip.storage_margin_gb")},
        ]},
        {"title": tr("settings.group.adb"), "desc": tr("settings.group.adb.desc"), "items": [
            {"key": "ADB_FAILURE_POLICY", "label": tr("settings.label.adb_failure_policy"), "type": "select",
             "choices": [
                 {"value": "pause_dialog", "label": tr("settings.choice.adb_policy.pause_dialog")},
                 {"value": "continue", "label": tr("settings.choice.adb_policy.continue")},
             ], "desc": tr("settings.tooltip.adb_failure_policy")},
            {"key": "ADB_RECOVER_MIN_INTERVAL_SEC", "label": tr("settings.label.adb_recover_interval"), "type": "float",
             "min": 0, "max": 300, "step": 1.0, "desc": tr("settings.tooltip.adb_recover_interval")},
            {"key": "ADB_RECOVERY_WAIT_SEC", "label": tr("settings.label.adb_recovery_wait"), "type": "int",
             "min": 0, "max": 3600, "desc": tr("settings.tooltip.adb_recovery_wait")},
        ]},
        {"title": tr("settings.group.battery"), "desc": tr("settings.group.battery.desc"), "items": [
            {"key": "BATTERY_LOW_THRESHOLD_PCT", "label": tr("settings.label.battery_low_threshold"), "type": "int",
             "min": 0, "max": 100, "desc": tr("settings.tooltip.battery_low_threshold")},
            {"key": "BATTERY_CRITICAL_PCT", "label": tr("settings.label.battery_critical"), "type": "int",
             "min": 0, "max": 100, "desc": tr("settings.tooltip.battery_critical")},
            {"key": "BATTERY_WAIT_MAX_SEC", "label": tr("settings.label.battery_wait_max"), "type": "int",
             "min": 0, "max": 86400, "desc": tr("settings.tooltip.battery_wait_max")},
            {"key": "BATTERY_CHECK_INTERVAL_SEC", "label": tr("settings.label.battery_check_interval"), "type": "int",
             "min": 30, "max": 86400, "desc": tr("settings.tooltip.battery_check_interval")},
            {"key": "DEVICE_TEMP_LIMIT_C", "label": tr("settings.label.device_temp_limit"), "type": "float",
             "min": 0, "max": 80, "step": 0.5, "desc": tr("settings.tooltip.device_temp_limit")},
        ]},
        {"title": tr("settings.group.backup"), "desc": tr("settings.group.backup.desc"), "items": [
            {"key": "BACKUP_TIMEOUT", "label": tr("settings.label.backup_timeout"), "type": "int", "min": 10, "max": 10000,
             "desc": tr("settings.tooltip.backup_timeout")},
            {"key": "BACKUP_ABSOLUTE_MAX_SECONDS", "label": tr("settings.label.backup_absolute_max"), "type": "int", "min": 60, "max": 86400,
             "desc": tr("settings.tooltip.backup_absolute_max")},
            {"key": "MIN_BACKUP_THROUGHPUT_MBPS", "label": tr("settings.label.min_throughput"), "type": "float", "min": 0, "max": 50,
             "desc": tr("settings.tooltip.min_throughput")},
            {"key": "BACKUP_CHECK_INTERVAL", "label": tr("settings.label.backup_check_interval"), "type": "int", "min": 1, "max": 120,
             "desc": tr("settings.tooltip.backup_check_interval")},
            {"key": "MIN_ACCEPT_ELAPSED_SECONDS", "label": tr("settings.label.min_accept_elapsed"), "type": "int", "min": 1, "max": 600,
             "desc": tr("settings.tooltip.min_accept_elapsed")},
            {"key": "BACKUP_STALLED_LIMIT", "label": tr("settings.label.stalled_limit"), "type": "int", "min": 0, "max": 600,
             "desc": tr("settings.tooltip.stalled_limit")},
            {"key": "RESTART_PHOTOS_AFTER_PUSH", "label": tr("settings.label.restart_photos"), "type": "bool",
             "desc": tr("settings.tooltip.restart_photos")},
        ]},
        {"title": tr("settings.group.night"), "desc": tr("settings.group.night.desc"), "items": [
            {"key": "ENABLE_NIGHT_LIGHT_MODE", "label": tr("settings.label.enable_night"), "type": "bool",
             "desc": tr("settings.tooltip.enable_night")},
            {"key": "SCREEN_BRIGHTNESS_NIGHT", "label": tr("settings.label.night_brightness"), "type": "int", "min": 0, "max": 255,
             "desc": tr("settings.tooltip.night_brightness")},
        ]},
        {"title": tr("settings.group.disconnect_log"), "desc": tr("settings.group.disconnect_log.desc"), "items": [
            {"key": "DISCONNECT_LOG_ENABLED", "label": tr("settings.label.disconnect_log_enabled"), "type": "bool",
             "desc": tr("settings.tooltip.disconnect_log_enabled")},
            {"key": "DISCONNECT_HEARTBEAT_INTERVAL_SEC", "label": tr("settings.label.disconnect_heartbeat_interval"), "type": "int",
             "min": 30, "max": 86400, "desc": tr("settings.tooltip.disconnect_heartbeat_interval"),
             "depends_on": "DISCONNECT_LOG_ENABLED"},
        ]},
        {"title": tr("settings.group.log"), "desc": tr("settings.group.log.desc"), "items": [
            {"key": "LOG_VERBOSITY", "label": tr("settings.label.log_verbosity"), "type": "select",
             "choices": [
                 {"value": "full", "label": tr("settings.log_verbosity.full")},
                 {"value": "standard", "label": tr("settings.log_verbosity.standard")},
                 {"value": "minimal", "label": tr("settings.log_verbosity.minimal")},
             ], "desc": tr("settings.tooltip.log_verbosity")},
        ]},
        {"title": tr("settings.group.web"), "desc": tr("settings.group.web.desc"), "items": [
            {"key": "WEB_SERVER_HOST", "label": tr("settings.label.web_host"), "type": "text",
             "desc": tr("settings.tooltip.web_host")},
            {"key": "WEB_SERVER_PORT", "label": tr("settings.label.web_port"), "type": "int", "min": 1, "max": 65535,
             "desc": tr("settings.tooltip.web_port")},
        ]},
    ]


def _read_health_series(bucket: str, keys: List[str], since: Optional[str] = None) -> Dict[str, Any]:
    """logs/health.jsonl (v1.7.18 の 切断解析ログ) を バケット 単位 に 集約 する 。
    残量 は 平均 、 温度 は 最高 、 空き容量 は 最小 (最も 逼迫 した 瞬間) を 採る 。
    データ が ない バケット は None 。
    v1.7.20: since ('YYYY-MM-DD HH:MM:SS') 指定 時 は それ 以降 の 記録 だけ を 対象 に する 。

    Aggregate logs/health.jsonl (the v1.7.18 disconnect-analysis log) per bucket.
    Level uses the average, temperature the max, free space the minimum (the tightest
    moment); empty buckets are None.
    v1.7.20: when `since` is given, only records at or after it are used."""
    import json as _json
    from pathlib import Path as _Path

    try:
        from nas2gp.core import settings as _s
        p = _Path(str(_s.WORK_ROOT)) / "logs" / "health.jsonl"
    except Exception:
        p = _Path("logs") / "health.jsonl"

    lvl_sum: Dict[str, float] = {}
    lvl_n: Dict[str, int] = {}
    temp_max: Dict[str, float] = {}
    free_min: Dict[str, float] = {}
    try:
        if p.exists():
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = _json.loads(line)
                    except Exception:
                        continue
                    ts = str(r.get("ts") or "")
                    if len(ts) < 13:
                        continue
                    if since and ts < str(since):
                        continue
                    k = (ts[:13] + ":00") if bucket == "hour" else ts[:10]
                    lv = r.get("level")
                    if isinstance(lv, (int, float)):
                        lvl_sum[k] = lvl_sum.get(k, 0.0) + float(lv)
                        lvl_n[k] = lvl_n.get(k, 0) + 1
                    tp = r.get("temp_c")
                    if isinstance(tp, (int, float)):
                        v = float(tp)
                        if k not in temp_max or v > temp_max[k]:
                            temp_max[k] = v
                    fg = r.get("free_gb")
                    if isinstance(fg, (int, float)):
                        v = float(fg)
                        if k not in free_min or v < free_min[k]:
                            free_min[k] = v
    except Exception:
        pass

    level: List[Any] = []
    temp: List[Any] = []
    free: List[Any] = []
    for k in keys:
        n = lvl_n.get(k, 0)
        level.append(round(lvl_sum[k] / n, 1) if n else None)
        temp.append(round(temp_max[k], 1) if k in temp_max else None)
        free.append(round(free_min[k], 2) if k in free_min else None)
    return {"level": level, "temp": temp, "free": free}


def create_app() -> FastAPI:
    from nas2gp.core import settings as web_settings

    app = FastAPI(title="NAS2GP Web UI", version=str(getattr(web_settings, "APP_VERSION", "0.0.0") or "0.0.0"))

    # v1.7.15: 診断 ページ の 「アプリ起動時刻」 用 に Web サーバ 起動 時刻 を 記録
    # v1.7.15: Record the web server start time for the diagnostics "app uptime".
    try:
        from nas2gp.core.diagnostics import set_runtime_started
        set_runtime_started(time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass

    base_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(base_dir, "templates")
    os.makedirs(templates_dir, exist_ok=True)
    static_dir = os.path.join(base_dir, "static")
    os.makedirs(static_dir, exist_ok=True)
    from jinja2 import Environment, FileSystemLoader
    _jinja_env = Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=True,
        cache_size=0,
        auto_reload=False,
    )
    templates = Jinja2Templates(directory=templates_dir)
    templates.env = _jinja_env

    # v1.7.16: 公開用 i18n。テンプレートから {{ tr('key') }} で参照可能にする。
    # tr() は core.i18n の単語バンクを レンダリング 時 に 参照 する ため 、
    # 設定 の LANGUAGE 変更 (reload_language) が 次回 リクエスト から 反映 さ れる。
    # v1.7.16: i18n for release. Templates can call {{ tr('key') }}; tr() reads the
    # core.i18n word bank at render time, so LANGUAGE changes apply from the next request.
    from nas2gp.core.i18n import tr as _i18n_tr

    def _tr(key):
        try:
            return _i18n_tr(key)
        except Exception:
            return key

    def _i18n_dict():
        """JS 側 で 使う 翻訳辞書 (window.I18N) を 返す。内部 キー (__*) は 除外。

        Returns the translation dict (window.I18N) for JS; internal keys (__*) excluded."""
        try:
            from nas2gp.core.i18n import WORDS
            return {k: v for k, v in dict(WORDS).items() if not str(k).startswith("__")}
        except Exception:
            return {}

    _jinja_env.globals["tr"] = _tr
    _jinja_env.globals["i18n_dict"] = _i18n_dict

    def _lan_ip() -> str:
        """v1.7.15: ヘッダ表示 用 の 実 LAN IP を 推定 する (0.0.0.0 の まま に し ない)。

        v1.7.15: Estimate the real LAN IP for the header (never leave it as 0.0.0.0)."""
        try:
            import socket as _sock
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                return str(s.getsockname()[0])
            finally:
                s.close()
        except Exception:
            try:
                import socket as _sock2
                return str(_sock2.gethostbyname(_sock2.gethostname()))
            except Exception:
                return "127.0.0.1"

    def _page_cfg() -> Dict[str, Any]:
        bind_host = str(getattr(web_settings, "WEB_SERVER_HOST", "0.0.0.0") or "0.0.0.0")
        return {
            "host": bind_host if bind_host not in ("0.0.0.0", "::", "") else _lan_ip(),
            "port": int(getattr(web_settings, "WEB_SERVER_PORT", 8000) or 8000),
            "interval": float(getattr(web_settings, "WEB_SSE_INTERVAL_SECONDS", 1.0) or 1.0),
            "strategy": str(getattr(web_settings, "BATCH_STRATEGY", "hybrid")),
            "max_bytes_gb": round(int(getattr(web_settings, "MAX_BYTES_PER_BATCH", 0) or 0) / (1024 ** 3), 2),
            "max_files": int(getattr(web_settings, "MAX_FILES_PER_BATCH", 30) or 30),
            "margin_gb": float(getattr(web_settings, "DEVICE_STORAGE_MARGIN_GB", 4.0) or 4.0),
        }

    def _render_page(request: Request, template_name: str, active: str, title_key: str) -> HTMLResponse:
        """v1.7.13: GUI の タブ 構成 に 合わせ た 複数 ページ を 共通 base.html で 描画 する 。
        v1.7.16: page_title は i18n キー を 受け取り 、 レンダリング 時 に 翻訳 する 。

        Renders the multi-page UI (mirroring the GUI tabs) via shared base.html;
        page_title is an i18n key translated at render time."""
        tpl = _jinja_env.get_template(template_name)
        html = tpl.render(request=request, cfg=_page_cfg(), active=active,
                          page_title=_tr(title_key),
                          app_version=str(getattr(web_settings, "APP_VERSION", "") or ""))
        return HTMLResponse(content=html)

    def _consent_block():
        """v1.10.0: 未同意 なら 409 応答 用 の 本文 を 返す (同意 済み なら None)。
        データ を 動かす / 消す API の 先頭 で 呼ぶ 。

        v1.10.0: Returns the 409 body when consent is missing (None when accepted).
        Called at the top of the APIs that move or delete data."""
        try:
            from nas2gp.core import consent as _consent
            if _consent.is_accepted():
                return None
        except Exception:
            return None
        return {"ok": False, "error": "consent_required", "message": _tr("web.consent.required")}

    _PAGES = [
        # (path, template, active_key, i18n title key)
        ("/", "index.html", "home", "web.nav.home"),
        ("/console", "console.html", "console", "web.nav.console"),
        ("/settings", "settings.html", "settings", "web.nav.settings"),
        ("/device", "device.html", "device", "web.nav.device"),
        ("/management", "management.html", "management", "web.nav.management"),
        ("/stats", "stats.html", "stats", "web.nav.stats"),
        ("/diagnostics", "diagnostics.html", "diagnostics", "web.nav.diagnostics"),
        ("/help", "help.html", "help", "web.nav.help"),
    ]

    def _make_page_handler(template_name: str, active: str, title_key: str):
        async def _handler(request: Request):
            return _render_page(request, template_name, active, title_key)
        return _handler

    for _path, _tpl_name, _active_key, _title_key in _PAGES:
        app.add_api_route(
            _path,
            _make_page_handler(_tpl_name, _active_key, _title_key),
            methods=["GET"],
            response_class=HTMLResponse,
            name=f"page_{_active_key}",
        )

    @app.get("/api/status")
    async def api_status():
        return RUNTIME.snapshot()

    @app.get("/api/adb/devices")
    async def api_adb_devices():
        """v1.7.13: 接続デバイス 一覧 (Web 画面 の 状態 バッジ 用)。
        connected = adb devices で state=device の 端末 が 1 台 以上
        shell_ok  = さらに adb shell true まで 通る か (見せかけ接続 の 判別)

        v1.7.13: Connected device list for the Web status badge.
        connected = at least one device in state "device"; shell_ok = also passes shell true."""
        result = {"ok": False, "connected": False, "shell_ok": False,
                  "devices": [], "error": None}
        try:
            from nas2gp.device.adb import ADB as _ADBD
            devs = _ADBD.list_devices_with_state() or []
            result["devices"] = devs
            result["connected"] = any(str(d.get("state")) == "device" for d in devs)
            if result["connected"]:
                try:
                    result["shell_ok"] = bool(_ADBD._probe_shell_true())
                except Exception:
                    result["shell_ok"] = False
            result["ok"] = True
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
        return result

    @app.get("/api/adb/device_info")
    def api_adb_device_info():
        """v1.7.16: 接続デバイス の 空き容量 / 本体温度 / バッテリー残量 (ホーム 表示 用)。
        adb shell (df / dumpsys battery) を 実行 する ため 同期 def に し 、
        FastAPI の スレッドプール で 実行 し て SSE ループ を 塞が ない ように する 。

        v1.7.16: Free space / temperature / battery for the home screen. Defined as a
        sync def so adb shell runs in FastAPI's thread pool without blocking SSE."""
        try:
            from nas2gp.device.adb import ADB as _ADBD
            return _ADBD.get_device_health()
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @app.get("/api/settings")
    async def api_settings():
        """v1.7.13: GUI 設定 (config_ui.json) の 全 キー を 返す 。
        旧版 は Web 専用 8 キー のみ で GUI と 差異 が あっ た ため 全項目 に 統一 。

        v1.7.13: Returns all GUI config (config_ui.json) keys, unified with the GUI."""
        def _v(k, default=None):
            try:
                return getattr(web_settings, k, default)
            except Exception:
                return default
        result = {}
        try:
            from nas2gp.core.config_loader import load_and_apply_gui_config
            result = dict(load_and_apply_gui_config(silent=True) or {})
        except Exception:
            result = {}
        result.pop("CONFIG_SCHEMA_VERSION", None)
        # config に 存在 し ない Web 専用 項目 は settings から 補完 (編集 対象 外 だ が 表示 用)
        # Fill in Web-only keys missing from config from settings (display only).
        result.setdefault("WEB_MAX_LOG_LINES", _v("WEB_MAX_LOG_LINES", 120))
        result.setdefault("WEB_SSE_INTERVAL_SECONDS", _v("WEB_SSE_INTERVAL_SECONDS", 1.0))
        return result

    @app.get("/api/settings/schema")
    async def api_settings_schema():
        """Web 設定画面 の 表示 メタ情報 (GUI SettingsTab 相当) を 返す 。

        Returns the display metadata for the Web settings screen (GUI SettingsTab equivalent)."""
        try:
            return {"ok": True, "groups": _settings_ui_schema()}
        except Exception as e:
            return {"ok": False, "groups": [], "error": f"{type(e).__name__}: {e}"}

    @app.get("/api/logs")
    async def api_logs(limit: int = 120):
        return {"logs": RUNTIME.recent_logs(max(1, min(500, limit)))}

    # ============================================================
    # v1.7.16: /stats 統計 ページ (ファイル一覧 + 工程別 完了状況)
    # v1.7.16: /stats statistics page (file list + per-stage completion status)
    # ============================================================
    @app.get("/api/files")
    def api_files(
        page: int = 1,
        page_size: int = 100,
        transfer: str = "all",
        gp: str = "all",
        delete: str = "all",
        search: str = "",
        sort: str = "id",
    ):
        """DB の 転送対象ファイル 一覧 を 返す (各工程 の 状態 を 並べた 表示 用)。
        転送 / GP / 削除 の 各列 を 独立 に 絞り込める (all/done/failed/remaining)。
        読み取り専用 別接続 + ページング で 負荷 を 抑え る。同期 def に より
        FastAPI の スレッドプール で 実行 し SSE ループ を 塞が ない。"""

        # Returns the list of transfer target files with each stage's status.
        # Each column (transfer / GP / delete) can be filtered independently.
        # Uses a read-only separate connection + paging; sync def runs in the thread pool.
        try:
            from nas2gp.storage.db_view import FileView
            with FileView() as fv:
                return fv.list_files(
                    page=page, page_size=page_size,
                    transfer=transfer, gp=gp, delete=delete,
                    search=search, sort=sort,
                )
        except Exception as e:
            return {"ok": False, "rows": [], "total": 0, "page": 1,
                    "page_count": 0, "error": f"{type(e).__name__}: {e}"}

    @app.get("/api/files/summary")
    def api_files_summary():
        """各工程 (転送 / GP / 削除) の 完了 / 失敗 / 残り 件数 と 容量 を 返す。

        Returns done / failed / remaining counts and bytes for each stage (transfer / GP / delete)."""
        try:
            from nas2gp.storage.db_view import FileView
            with FileView() as fv:
                return fv.summary()
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @app.get("/api/stats/timeseries")
    def api_stats_timeseries(range: str = "24h", start: Optional[str] = None, end: Optional[str] = None):
        """v1.7.19: 統計 時系列 グラフ 用 の 集計 を 返す (DB + health.jsonl)。
        DB 集計 は 読み取り専用 別接続 。端末 健康 (残量 / 温度 / 空き容量) は logs/health.jsonl
        を バケット 単位 に 集約 する 。同期 def に より スレッドプール で 実行 する 。
        v1.12.0: start/end ('YYYY-MM-DD') で 過去 の 日付 範囲 を 指定 できる 。

        v1.7.19: Returns time-series aggregates for the stats graphs (DB + health.jsonl).
        DB aggregation uses a read-only connection; device health (level/temp/free) is
        bucketed from logs/health.jsonl. A sync def runs it in the thread pool.
        v1.12.0: start/end ('YYYY-MM-DD') select an explicit past date range."""
        try:
            from nas2gp.storage.db_view import FileView
            with FileView() as fv:
                baseline = fv.get_stats_baseline()
                data = fv.timeseries(range, baseline=baseline, start=start, end=end)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        data["baseline"] = baseline
        try:
            data["health"] = _read_health_series(
                str(data.get("bucket") or "hour"),
                list(data.get("keys") or []),
                since=str(data.get("since") or "") or None,
            )
        except Exception:
            data["health"] = {"level": [], "temp": [], "free": []}
        return data

    @app.post("/api/stats/clear_history")
    def api_stats_clear_history():
        """v1.7.20: 統計 の 『過去履歴クリア』。基準時刻 (現在 時刻) を 記録 し 、
        これ 以降 の データ だけ を グラフ に 表示 する (DB から は 消さ ない)。

        v1.7.20: Record a baseline (now) so only data from now on is charted
        (non-destructive; nothing is deleted from the DB)."""
        try:
            from datetime import datetime as _dt
            from nas2gp.storage.db_view import resolve_db_path
            import sqlite3 as _sq
            dbp = resolve_db_path()
            if not dbp or not os.path.exists(dbp):
                return {"ok": False, "error": "DB file not found"}
            ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = _sq.connect(dbp, timeout=5.0)
            try:
                conn.execute("PRAGMA busy_timeout = 5000")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO app_meta(key, value) VALUES('stats_baseline', ?)",
                    (ts,),
                )
                conn.commit()
            finally:
                conn.close()
            return {"ok": True, "baseline": ts}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @app.delete("/api/stats/clear_history")
    def api_stats_clear_history_reset():
        """v1.7.20: 基準時刻 を 解除 し 、 全履歴 を 再表示 する 。

        v1.7.20: Clear the baseline so the full history is shown again."""
        try:
            from nas2gp.storage.db_view import resolve_db_path
            import sqlite3 as _sq
            dbp = resolve_db_path()
            if not dbp or not os.path.exists(dbp):
                return {"ok": False, "error": "DB file not found"}
            conn = _sq.connect(dbp, timeout=5.0)
            try:
                conn.execute("PRAGMA busy_timeout = 5000")
                conn.execute("DELETE FROM app_meta WHERE key='stats_baseline'")
                conn.commit()
            finally:
                conn.close()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @app.get("/api/logs/sse")
    async def sse_logs_only():
        async def gen():
            import json
            try:
                interval = float(getattr(web_settings, "WEB_SSE_INTERVAL_SECONDS", 1.0) or 1.0)
            except Exception:
                interval = 1.0
            last_ts = 0
            while True:
                rows = RUNTIME.recent_logs(300)
                data = {
                    "logs": rows,
                    "count": len(rows),
                    "ts": int(time.time() * 1000),
                    "state_snapshot": RUNTIME.snapshot(),
                }
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                await _async_sleep(max(0.2, min(5.0, interval)))
        return StreamingResponse(gen(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        })

    # ============================================================
    # v1.7.15: 診断 / 管理 (GUI 版 と 同等 の 機能)
    # v1.7.15: Diagnostics / management (equivalent to the GUI version)
    # ============================================================
    @app.get("/api/diagnostics")
    async def api_diagnostics():
        """GUI 診断 タブ と 同等 の 項目 (3カード + 詳細14行) を 返す。

        Returns the same items as the GUI diagnostics tab (3 cards + 14 detail rows)."""
        try:
            from nas2gp.core.diagnostics import collect_diagnostics
            return collect_diagnostics()
        except Exception as e:
            return {"ok": False, "cards": [], "rows": [], "error": f"{type(e).__name__}: {e}"}

    @app.get("/api/diagnostics/bugreport")
    async def api_bug_report():
        """ログ 3 件 + config + DB + 診断 結果 を ZIP 化 し て ダウンロード さ せ る。

        Zips 3 logs + config + DB + diagnostics results and returns it as a download."""
        try:
            from nas2gp.core.diagnostics import build_bug_report_zip
            data, name = build_bug_report_zip()
            return Response(
                content=data,
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{name}"'},
            )
        except Exception as e:
            return JSONResponse(
                {"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500
            )

    @app.post("/api/open_path")
    async def api_open_path(payload: Optional[Dict[str, Any]] = None):
        """v1.7.15: サーバ PC 上 で フォルダ を 開く (GUI の 「フォルダを開く」 相当)。
        target: logs / db / config / photos

        v1.7.15: Opens a folder on the server PC (equivalent to the GUI "open folder").
        target: logs / db / config / photos"""
        target = str((payload or {}).get("target") or "").strip().lower()
        path = None
        try:
            from nas2gp.core import settings as _s
            from pathlib import Path as _P
            work_root = _P(str(getattr(_s, "WORK_ROOT", None) or os.getcwd()))
            if target == "logs":
                from nas2gp.core.logger import Logger
                p = _P(str(getattr(Logger, "LOG_DIR", work_root / "logs")))
                path = p if p.is_absolute() else (work_root / p)
            elif target == "db":
                # 書き込み側 が 実際 に 開く DB (settings の 1 箇所 を 正) の フォルダ。
                # Folder of the DB actually opened by the writer (settings single truth).
                raw = _P(str(_s.resolve_db_file()))
                path = raw.parent
            elif target == "config":
                path = work_root / "config"
            elif target == "photos":
                raw = _P(str(getattr(_s, "PHOTO_DIR", "")))
                path = raw if raw.is_absolute() else (work_root / raw)
            else:
                return {"ok": False, "error": f"unknown target: {target!r}"}
            path = _P(path)
            path.mkdir(parents=True, exist_ok=True)
            from nas2gp.core.util import open_in_file_manager
            open_in_file_manager(str(path))
            return {"ok": True, "path": str(path)}
        except Exception as e:
            return {"ok": False, "path": str(path or ""), "error": f"{type(e).__name__}: {e}"}

    # ------------------------------------------------------------
    # v1.7.15: 管理 (メンテナンス) API - GUI 管理 タブ 相当
    #   各 リセット は 元 ファイル を _OLD_ 接頭辞 で rename 退避 する。
    # v1.7.15: Maintenance API (equivalent to the GUI management tab); each reset
    #   renames the original files with an _OLD_ prefix as a backup.
    # ------------------------------------------------------------
    @app.post("/api/maintenance/reset_progress")
    async def api_maintenance_reset_progress():
        try:
            from nas2gp.core.maintenance import reset_progress
            return reset_progress()
        except Exception as e:
            return {"ok": False, "renamed": 0, "errors": [], "error": f"{type(e).__name__}: {e}"}

    @app.post("/api/maintenance/reset_settings")
    async def api_maintenance_reset_settings():
        try:
            from nas2gp.core.maintenance import reset_settings
            return reset_settings()
        except Exception as e:
            return {"ok": False, "renamed": 0, "errors": [], "error": f"{type(e).__name__}: {e}"}

    @app.post("/api/maintenance/factory_reset")
    async def api_maintenance_factory_reset():
        try:
            from nas2gp.core.maintenance import factory_reset
            return factory_reset()
        except Exception as e:
            return {"ok": False, "renamed": 0, "errors": [], "error": f"{type(e).__name__}: {e}"}

    # v1.8.0: 端末 側 の 作業フォルダ (転送先) の 取得 / 変更 / ブラウズ。
    #   安全 の ため DCIM 配下 のみ を 対象 と する (Google Photos の 自動 バックアップ
    #   対象 で あ る こと を 担保 する ため)。
    # v1.8.0: get / change / browse the device-side working folder. DCIM-only for
    #   safety (keeps it a Google Photos auto-backup target).
    _DEVICE_DCIM_ROOT = "/sdcard/DCIM"

    def _norm_dcim_path(path: Any) -> Optional[str]:
        """端末 パス を 正規化 し 、 DCIM 配下 なら 返す 。 不正 なら None 。
        DCIM ルート 自体 も ブラウズ 用 に は 許可 する 。

        Normalize a device path; returns it when under DCIM (the DCIM root itself is
        allowed for browsing), else None."""
        p = str(path or "").strip().rstrip("/")
        if not p:
            p = _DEVICE_DCIM_ROOT
        if p == _DEVICE_DCIM_ROOT or p.startswith(_DEVICE_DCIM_ROOT + "/"):
            return p
        return None

    @app.get("/api/device/workdir")
    async def api_device_workdir():
        """現在 の 作業フォルダ と 端末 空き容量 を 返す。

        Return the current working folder and the device free space."""
        result = {"ok": False, "path": "", "free_gb": 0.0, "error": None}
        try:
            from nas2gp.core import settings as _s_wd
            result["path"] = str(getattr(_s_wd, "DEVICE_WORK_DIR", "") or "")
            from nas2gp.device.device import Device as _PxW
            px = _PxW()
            try:
                px.ensure_adb_alive(aggressive=False, shell_probe=False, publish_unrecoverable=False)
            except Exception:
                pass
            result["free_gb"] = float(px.get_storage_root_free_gb() or 0.0)
            result["ok"] = True
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
        return result

    @app.get("/api/device/browse")
    async def api_device_browse(request: Request):
        """作業フォルダ 選択 用 に 、 DCIM 配下 の サブフォルダ 一覧 を 返す。

        List DCIM subfolders for choosing the working folder."""
        result = {"ok": False, "path": _DEVICE_DCIM_ROOT, "parent": None, "folders": [], "error": None}
        try:
            target = _norm_dcim_path(request.query_params.get("path"))
            if target is None:
                result["error"] = "DCIM 配下 の パス を 指定 し て ください 。"
                return result
            result["path"] = target
            if target != _DEVICE_DCIM_ROOT:
                result["parent"] = target.rsplit("/", 1)[0] or _DEVICE_DCIM_ROOT
            from nas2gp.device.device import Device as _PxB
            px = _PxB()
            try:
                px.ensure_adb_alive(aggressive=False, shell_probe=False, publish_unrecoverable=False)
            except Exception:
                pass
            result["folders"] = list(px.list_subfolders(target) or [])
            result["ok"] = True
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
        return result

    @app.post("/api/device/workdir")
    async def api_device_set_workdir(payload: Optional[Dict[str, Any]] = None):
        """作業フォルダ を 変更 する 。 DCIM 配下 の サブフォルダ のみ 受理 し 、
        無けれ ば 作成 する 。 config_ui.json へ 保存 し settings へ も 反映 する。

        Change the working folder. Accepts only DCIM subfolders, creating the folder
        if missing; persists to config_ui.json and applies to settings."""
        result = {"ok": False, "path": "", "error": None}
        try:
            target = _norm_dcim_path((payload or {}).get("path"))
            if target is None or target == _DEVICE_DCIM_ROOT:
                result["error"] = "DCIM 配下 の サブフォルダ を 指定 し て ください 。"
                return result
            from nas2gp.device.device import Device as _PxS
            px = _PxS()
            try:
                px.ensure_adb_alive(aggressive=False, shell_probe=False, publish_unrecoverable=False)
            except Exception:
                pass
            if not px.create_dir(target):
                result["error"] = f"フォルダ を 作成 でき ませ ん で し た: {target}"
                return result
            from nas2gp.core.config_loader import (
                load_and_apply_gui_config as _load_cfg,
                save_gui_config as _save_cfg,
                _apply_runtime_settings as _apply_cfg,
                _DEFAULT_CONFIG as _DEF_CFG,
            )
            try:
                merged = dict(_load_cfg(silent=True) or {})
            except Exception:
                merged = dict(_DEF_CFG)
            merged["DEVICE_WORK_DIR"] = target
            _save_cfg(merged, silent=True)
            _apply_cfg(merged, silent=True)
            result["path"] = target
            result["ok"] = True
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
        return result

    @app.get("/api/device/delete_uploaded/preview")
    async def api_device_delete_uploaded_preview():
        """v1.13.6: 「作業フォルダの中身を削除」の事前確認用。現在の作業フォルダ配下の
        ファイル数と合計サイズを端末から実測して返す。DB の状態は条件にしない
        (フォルダに実在するものがすべて対象)。

        v1.13.6: preview for "delete the working folder contents". Measures the file
        count and total size under the current working folder on the device. DB state
        is not used as a condition (everything physically present is a target).
        """
        _blk = _consent_block()
        if _blk:
            return JSONResponse(status_code=409, content=_blk)
        result = {"ok": False, "path": "", "total": 0, "bytes": 0, "error": None}
        try:
            from nas2gp.core import settings as _s_dp
            from nas2gp.device.device import Device as _PxP
            result["path"] = str(getattr(_s_dp, "DEVICE_WORK_DIR", "") or "")
            px = _PxP()
            _cnt, _bytes = px.measure_folder(result["path"])
            result["total"] = int(_cnt or 0)
            result["bytes"] = int(_bytes or 0)
            result["ok"] = True
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
        return result

    @app.post("/api/device/delete_uploaded")
    async def api_device_delete_uploaded():
        """
        v1.13.6: 現在の作業フォルダ配下をすべて削除する (フォルダ自体は残す)。条件は
        付けず、Google Photos 未完了のファイルも含めて消す。削除後は転送済みの行の
        削除状態を DONE へ整合させる。破壊的な操作のため、同意ゲートで保護する。

        v1.13.6: Deletes everything under the current working folder, keeping the folder
        itself. No conditions: files not yet backed up to Google Photos are also removed.
        Afterwards the delete state of transferred rows is reconciled to DONE. This is
        destructive, so it is protected by the consent gate.
        """
        _blk = _consent_block()
        if _blk:
            return JSONResponse(status_code=409, content=_blk)
        result = {"ok": False, "path": "", "total": 0, "bytes": 0,
                  "db_reconciled": 0, "free_gb_after": None, "logs": [], "error": None}
        try:
            from nas2gp.core import settings as _s_dp2
            from nas2gp.device.device import Device as _Px2
            from nas2gp.core.logger import Logger as _Lg2
            from nas2gp.storage.db import Database as _Db2
            result["path"] = str(getattr(_s_dp2, "DEVICE_WORK_DIR", "") or "")
            if not result["path"] or result["path"].rstrip("/") == "/sdcard":
                result["error"] = "作業フォルダ が 未設定 です 。"
                return result
            px2 = _Px2()
            try:
                _cnt, _bytes = px2.measure_folder(result["path"])
                result["total"] = int(_cnt or 0)
                result["bytes"] = int(_bytes or 0)
            except Exception:
                pass
            _Lg2.info(f"[Web][作業フォルダ全削除] 開始 対象={result['path']} 件数={result['total']}")
            _ok = False
            try:
                _ok = bool(px2.delete_folder_contents(result["path"]))
            except Exception as _e_del:
                result["logs"].append(f"削除 例外: {_e_del}")
            if not _ok:
                result["error"] = "削除 に 失敗 し まし た (ADB 接続 を 確認 し て ください)。"
                return result
            result["ok"] = True
            # 転送 済み 行 の 削除 状態 を DONE へ 整合 さ せる (存在 し ない ファイル へ の
            # 空振り 再試行 を 防ぐ)。
            # Reconcile the delete state of transferred rows to DONE (avoids retrying
            # files that no longer exist).
            try:
                _db2 = _Db2()
                try:
                    _fixed = int(_db2.mark_all_phone_delete_pending_done() or 0)
                    if _fixed:
                        _db2.commit()
                    result["db_reconciled"] = _fixed
                finally:
                    try:
                        _db2.close()
                    except Exception:
                        pass
            except Exception as _e_db:
                result["logs"].append(f"DB 整合 スキップ: {_e_db}")
            try:
                result["free_gb_after"] = float(px2.get_storage_root_free_gb() or 0.0)
            except Exception:
                result["free_gb_after"] = None
            _Lg2.success(
                f"[Web][作業フォルダ全削除] 完了 件数={result['total']} DB整合={result['db_reconciled']}"
            )
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            try:
                from nas2gp.core.logger import Logger as _LgE
                _LgE.error(f"[Web][作業フォルダ全削除] 全体 例外: {result['error']}")
            except Exception:
                pass
        return result

    @app.get("/api/device/requeue_incomplete/preview")
    async def api_device_requeue_incomplete_preview():
        """v1.13.5: 「未 完了 を 再 転送 対象 に 戻す」 の 事前 確認 用。
        転送 / Google Photos / 端末削除 の いずれ か が 未 完了 の まま 残っ て い る 行 の
        件数 と 容量 、 および その うち 端末 から 既に 消え て い る (削除 DONE) 件数 を
        返す 。 既に 3 工程 とも PENDING (初期値) の 行 は 変化 し ない ため 対象 に 含め ない 。
        端末 に 残っ て い る 分 は 再 送信 さ れ ず (同名 SKIP)、 バックアップ 待ち から
        やり直さ れる 。

        v1.13.5: preview for "re-queue unfinished files". Returns the count/size of rows with
        any unfinished stage (transfer / Google Photos / device delete), excluding rows
        already fully PENDING (initial), plus how many are already gone from the device.
        """
        _blk = _consent_block()
        if _blk:
            return JSONResponse(status_code=409, content=_blk)
        result = {"ok": False, "total": 0, "bytes": 0, "gone": 0, "error": None}
        try:
            from nas2gp.storage.db import Database as _DbRQ
            _db = _DbRQ()
            try:
                _sum = _db.get_incomplete_transfer_summary() or {}
            finally:
                try:
                    _db.close()
                except Exception:
                    pass
            result["total"] = int(_sum.get("total") or 0)
            result["bytes"] = int(_sum.get("bytes") or 0)
            result["gone"] = int(_sum.get("gone") or 0)
            result["ok"] = True
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
        return result

    @app.post("/api/device/requeue_incomplete")
    async def api_device_requeue_incomplete():
        """v1.13.5: 3 工程 (転送 / Google Photos / 端末削除) の いずれ か が 未 完了 の 行 を
        再 転送 対象 (phone_transfer_state='PENDING') へ 戻す 。 既に 3 工程 とも PENDING
        の 初期値 行 は 変化 し ない ため 対象 外 。 端末 から 既に 消え て い る 分 は NAS から
        再 送 さ れ 、 端末 に 残っ て い る 分 は 同名 SKIP で 実 送信 せ ず に バックアップ 待ち ・
        削除 工程 だけ が やり直さ れる 。 Google Photos 完了 済み は すべて 3 工程 DONE の
        ため 対象 に 入ら ず 、 完了 済み GP を 巻き戻す こと は ない 。 NAS の 原本 は 削除 し ない 。

        v1.13.5: re-queue rows with any unfinished stage (transfer / Google Photos / device
        delete); rows already fully PENDING (initial) are excluded. Files gone from the device
        are pushed again; files still on the device are skipped and only the backup-wait /
        delete phases are retried. Completed GP rows are always fully DONE, so no completed GP
        state is rolled back. No NAS original is deleted.
        """
        _blk = _consent_block()
        if _blk:
            return JSONResponse(status_code=409, content=_blk)
        result = {"ok": False, "requeued": 0, "bytes": 0, "error": None}
        try:
            from nas2gp.storage.db import Database as _DbRQ
            from nas2gp.core.logger import Logger as _LgRQ
            _db = _DbRQ()
            try:
                _sum = _db.get_incomplete_transfer_summary() or {}
                _n = int(_db.requeue_incomplete_transfers() or 0)
                if _n:
                    _db.commit()
            finally:
                try:
                    _db.close()
                except Exception:
                    pass
            result["requeued"] = _n
            result["bytes"] = int(_sum.get("bytes") or 0)
            result["ok"] = True
            _LgRQ.info(
                f"[Web][未完了再転送] 再転送対象に戻しました "
                f"件数={_n} / 端末に無い={int(_sum.get('gone') or 0)}"
            )
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            try:
                from nas2gp.core.logger import Logger as _LgRQ2
                _LgRQ2.error(f"[Web][未完了再転送] 失敗: {result['error']}")
            except Exception:
                pass
        return result

    @app.post("/api/device/delete_dcim_all")
    async def api_device_delete_dcim_all(payload: Optional[Dict[str, Any]] = None):
        """
        v1.11.0: /sdcard/DCIM 配下 を 全 削除 する (個人 の 写真 も 含む)。
        バックアップ 未 完了 の 写真 も 消える 危険 な 操作 の ため 、 Web UI の
        二 重 確認 (2 チェック) を 経 た うえ で confirm=true を 受け取る 。
        未 同意 の 場合 は 409 を 返す 。

        v1.11.0: Wipes everything under /sdcard/DCIM (personal photos included).
        Because it also erases photos not yet backed up, it requires the Web UI's
        double confirmation (2 checkboxes) plus an explicit confirm=true. Returns
        409 when consent is missing.
        """
        _blk = _consent_block()
        if _blk:
            return JSONResponse(status_code=409, content=_blk)
        if not bool((payload or {}).get("confirm")):
            return {"ok": False, "error": "confirm_required",
                    "message": _tr("web.device.dcim_confirm_final")}
        result = {"ok": False, "targets": ["/sdcard/DCIM"], "logs": [], "error": None}
        try:
            from nas2gp.device.device import Device as _PxD
            from nas2gp.core.logger import Logger as _LgD
            from nas2gp.storage.db import Database as _DbD
            px = _PxD()
            _LgD.info("[Web][DCIM 全削除] 開始 targets=['/sdcard/DCIM']")
            try:
                _ens = px.ensure_adb_alive(aggressive=True, shell_probe=True, publish_unrecoverable=True)
                result["logs"].append(f"ensure_adb_alive → {_ens}")
            except Exception as e_ensure:
                result["logs"].append(f"ensure_adb_alive exception (続行): {e_ensure}")
            ok = bool(px.delete_dcim_all())
            result["ok"] = ok
            result["logs"].append("実行: /sdcard/DCIM 配下 を 全削除" if ok
                                  else "失敗: /sdcard/DCIM 配下 の 削除 に 失敗")
            if ok:
                # DCIM 全削除 に 伴い 、 削除 待ち DB を 一括 で DONE に 整合 さ せ る 。
                # Reconcile all pending deletions to DONE after the whole-DCIM wipe.
                try:
                    _db = _DbD()
                    try:
                        fixed = int(_db.mark_all_phone_delete_pending_done() or 0)
                        if fixed:
                            _db.commit()
                        result["db_delete_reconciled"] = fixed
                        result["logs"].append(f"DB 整合 (削除待ち DONE 化): {fixed} 件")
                    finally:
                        try:
                            _db.close()
                        except Exception:
                            pass
                except Exception as e_db:
                    result["logs"].append(f"DB 整合 スキップ: {e_db}")
            try:
                result["free_gb_after"] = float(px.get_storage_root_free_gb() or 0.0)
            except Exception:
                result["free_gb_after"] = 0.0
            if ok:
                _LgD.success(f"[Web][DCIM 全削除] 完了: /sdcard/DCIM / 空き {result['free_gb_after']} GB")
            else:
                _LgD.error("[Web][DCIM 全削除] 失敗: /sdcard/DCIM")
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            try:
                from nas2gp.core.logger import Logger as _LgE2
                _LgE2.error(f"[Web][DCIM 全削除] 全体 例外: {result['error']}")
            except Exception:
                pass
        return result

    @app.post("/api/device/reconcile_delete")
    async def api_device_reconcile_delete():
        """v1.13.3: 端末 に 実在 する の に DB が 「削除 済み (DONE)」 と 記録 し て
        いる ファイル を 検出 し 、 削除 待ち (PENDING) へ 戻す 整合 ツール 。
        過去 バージョン の バグ (再転送 時 に 削除 状態 が DONE の まま 残り 、 削除
        対象 から 永久 に 外れ る) で 生じ た DB と 実機 の ズレ を 修復 する 。
        実 ファイル は 削除 せず 、 状態 を 戻す だけ (削除 は 別 の 削除 操作 で 行う)。

        v1.13.3: reconciliation tool. Detects files that physically exist on the
        device while the DB records them as already deleted (DONE), and re-queues
        them (PENDING). It repairs the mismatch left by the older bug (re-transfer
        kept the delete state at DONE, permanently excluding the file from deletion).
        It only fixes the state; it does not delete any real file.
        """
        _blk = _consent_block()
        if _blk:
            return JSONResponse(status_code=409, content=_blk)
        result = {"ok": False, "device_files": 0, "stale": 0, "fixed": 0,
                  "sample": [], "error": None}
        try:
            from nas2gp.device.adb import ADB as _ADB
            from nas2gp.device.device import Device as _DevRC
            from nas2gp.core import settings as _s_rc
            from nas2gp.storage.db import Database as _DbRC
            from nas2gp.core.logger import Logger as _LgRC
            work = str(getattr(_s_rc, "DEVICE_WORK_DIR", "") or "").rstrip("/")
            if not work:
                result["error"] = "作業 フォルダ が 未設定 です 。"
                return result
            _dev = _DevRC()
            try:
                _dev.ensure_adb_alive(aggressive=True, shell_probe=True, publish_unrecoverable=True)
            except Exception:
                pass
            try:
                _names = set(str(x).strip() for x in (_ADB.list_dir(work) or []) if str(x).strip())
            except Exception as e_ls:
                result["error"] = f"端末 の 一覧 取得 に 失敗 し ま し た: {e_ls}"
                return result
            result["device_files"] = len(_names)
            _db = _DbRC()
            try:
                _done_rows = list(_db.get_phone_delete_done_files() or [])
            finally:
                try:
                    _db.close()
                except Exception:
                    pass
            _path_by_name = {}
            for (_p, fn) in _done_rows:
                if fn:
                    _path_by_name[str(fn)] = _p
            _stale_paths = [_path_by_name[fn] for fn in _names if fn in _path_by_name]
            result["stale"] = len(_stale_paths)
            result["sample"] = sorted([fn for fn in _names if fn in _path_by_name])[:10]
            if not _stale_paths:
                result["ok"] = True
                return result
            try:
                _db_w = _DbRC()
                try:
                    _fixed = int(_db_w.mark_phone_delete_pending_bulk(_stale_paths) or 0)
                    if _fixed:
                        _db_w.commit()
                finally:
                    try:
                        _db_w.close()
                    except Exception:
                        pass
            except Exception as e_db:
                result["error"] = f"DB 更新 に 失敗: {e_db}"
                return result
            result["fixed"] = _fixed
            result["ok"] = True
            _LgRC.success(
                f"[Web][削除整合] 端末実在={len(_names)} / DB削除済みとズレ={len(_stale_paths)} / PENDINGへ復帰={_fixed}"
            )
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            try:
                from nas2gp.core.logger import Logger as _LgRCe
                _LgRCe.error(f"[Web][削除整合] 全体 例外: {result['error']}")
            except Exception:
                pass
        return result

    @app.get("/api/config/schema")
    async def api_config_schema():
        try:
            from nas2gp.core.config_loader import _DEFAULT_CONFIG
            return {"ok": True, "defaults": dict(_DEFAULT_CONFIG)}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @app.get("/api/consent")
    async def api_consent_get():
        """v1.10.0: 同意 状態 を 返す (同意 済み か / 同意 日時 / その 時 の バージョン)。

        v1.10.0: Returns the consent state (accepted? / accepted at / version)."""
        from nas2gp.core import consent as _consent
        data = _consent.load_consent()
        return {
            "ok": True,
            "accepted": bool(data.get("accepted")),
            "accepted_at": str(data.get("accepted_at") or ""),
            "version": str(data.get("version") or ""),
        }

    @app.post("/api/consent")
    async def api_consent_accept():
        """v1.10.0: 同意 を 記録 する (1 回 だけ 確認 し 、 以降 は consent.json で 記憶)。

        v1.10.0: Records the acceptance (asked only once; remembered in consent.json)."""
        from nas2gp.core import consent as _consent
        ver = str(getattr(web_settings, "APP_VERSION", "") or "")
        try:
            data = _consent.accept(ver)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        try:
            from nas2gp.core.logger import Logger as _LgC
            _LgC.info(f"[Web][同意] 免責事項に同意しました (v{ver})")
        except Exception:
            pass
        return {"ok": True, "accepted": True,
                "accepted_at": str(data.get("accepted_at") or ""), "version": ver}

    @app.delete("/api/consent")
    async def api_consent_reset():
        """v1.10.0: 同意 記録 を 消す (次回 ページ 表示 で 確認 画面 が 再表示 さ れる)。

        v1.10.0: Clears the consent record (the gate reappears on the next page load)."""
        from nas2gp.core import consent as _consent
        ok = _consent.reset()
        if ok:
            try:
                from nas2gp.core.logger import Logger as _LgC
                _LgC.info("[Web][同意] 同意記録をリセットしました")
            except Exception:
                pass
        return {"ok": bool(ok)}

    @app.put("/api/settings")
    async def api_settings_save(payload: Optional[Dict[str, Any]] = None):
        payload = payload if isinstance(payload, dict) else {}
        result = {"ok": False, "saved": {}, "warnings": [], "error": None}
        try:
            from nas2gp.core.config_loader import (
                load_and_apply_gui_config,
                save_gui_config,
                _apply_runtime_settings,
                _DEFAULT_CONFIG,
                _CONFIG_REQUIRED_KEYS,
            )
            base = {}
            try:
                base = dict(load_and_apply_gui_config(silent=True) or {})
            except Exception as e_load:
                result["warnings"].append(f"既存 config 読み込み 失敗 (新規 作成): {e_load}")
                base = dict(_DEFAULT_CONFIG)
            allowed = set(_CONFIG_REQUIRED_KEYS) - {"CONFIG_SCHEMA_VERSION"}
            merged = dict(base)
            for k, v in payload.items():
                if k not in allowed:
                    result["warnings"].append(f"スキップ (許可 さ れ て い ない キー): {k}")
                    continue
                merged[k] = v
                result["saved"][k] = v
            save_gui_config(merged, silent=True)
            _apply_runtime_settings(merged, silent=True)
            result["ok"] = True
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
        return result

    @app.get("/api/resume_status")
    async def api_resume_status():
        """v1.13.9: 前回 の 途中 停止 チェックポイント の 有無 を UI へ 返す 。
        v1.13.9: Report whether a previous interrupted-run checkpoint exists."""
        has_cp = False
        info: Dict[str, Any] = {}
        try:
            from nas2gp.storage.db import Database
            db = Database()
            try:
                cp = db.get_resume_checkpoint()
            finally:
                try:
                    db.close()
                except Exception:
                    pass
            if isinstance(cp, dict) and cp:
                has_cp = True
                info = {
                    "batch_index": cp.get("batch_index"),
                    "total_batches": cp.get("total_batches"),
                    "phase_no": cp.get("phase_no"),
                    "file_count": len(cp.get("batch_files") or []),
                    "saved_at": cp.get("saved_at"),
                }
        except Exception:
            has_cp = False
        return {"ok": True, "has_checkpoint": has_cp, "checkpoint": info}

    @app.post("/api/start")
    async def api_start(payload: Optional[Dict[str, Any]] = None):
        _blk = _consent_block()
        if _blk:
            return JSONResponse(status_code=409, content=_blk)
        try:
            from nas2gp.core.config_loader import load_and_apply_gui_config
            cfg = load_and_apply_gui_config(silent=True)
        except Exception:
            cfg = {}
        resume = False
        if isinstance(payload, dict):
            resume = bool(payload.get("resume"))
            cfg.update({k: v for k, v in payload.items() if k in {
                "BATCH_STRATEGY", "MAX_FILES_PER_BATCH", "MAX_BYTES_PER_BATCH",
                "DEVICE_STORAGE_MARGIN_GB", "PHOTO_DIR",
            }})
        ok = RUNTIME.start(cfg, resume=resume)
        return {"ok": ok, "running": RUNTIME.snapshot()["running"]}

    @app.post("/api/stop")
    async def api_stop():
        RUNTIME.stop()
        return {"ok": True, "state": RUNTIME.snapshot()}

    @app.post("/api/stop_batch")
    async def api_stop_batch():
        """v1.7.15: 「今回のバッチで終了」 。 きり の 良い バッチ 区切り で 停止 する。

        v1.7.15: "Finish after this batch"; stops at a clean batch boundary."""
        RUNTIME.stop_after_batch()
        return {"ok": True, "state": RUNTIME.snapshot()}

    @app.get("/progress/sse")
    async def sse_progress():
        async def gen():
            try:
                interval = float(getattr(web_settings, "WEB_SSE_INTERVAL_SECONDS", 1.0) or 1.0)
            except Exception:
                interval = 1.0
            while True:
                data = {
                    "state": RUNTIME.snapshot(),
                    "logs": RUNTIME.recent_logs(50),
                    "ts": int(time.time()),
                }
                import json
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                await _async_sleep(max(0.2, interval))
        return StreamingResponse(gen(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        })

    async def _async_sleep(seconds: float):
        import asyncio
        await asyncio.sleep(seconds)

    return app


__all__ = ["create_app", "WebSyncRuntime", "RUNTIME"]
