# ============================================
# FILE: pixel_sync/uploader.py
# VERSION: 4.1.0
# ============================================

from datetime import datetime
import time

from pixel_sync.core.config import COMMIT_INTERVAL
from pixel_sync.storage.db import Database
from pixel_sync.worker.backup_waiter import UploadWorker
from pixel_sync.core.i18n import tr
from datetime import timedelta
from pathlib import Path

from pixel_sync.core.settings import (
    RESTART_PHOTOS_AFTER_PUSH,
    FAILSAFE_RECOVERY_THRESHOLD_PER_BATCH,
    FAILSAFE_RESTART_ON_BACKUP_TIMEOUT,
    FLOW_FAIL_THRESHOLD_BATCH_FAIL_COUNT,
)
from pixel_sync.core import settings as _settings_module
from pixel_sync.core import events as _events
from pixel_sync.core import control as _control


def _publish_phase(phase, detail="", title=None, phase_no=None, total_phases=None, batch_idx=None):
    """v1.7.16: 現在 の 処理 フェーズ を Web UI / GUI へ 通知 する (バッチ 内 工程 インジケータ 用)。
    phase 例: CAPACITY / GP_STOP / UPLOAD / MEDIA_SCAN / GP_START / WAIT_BACKUP / DELETE
    title: コンソール 見出し と 同じ 文言 (tr() 由来) を 載せ る 場合 に 指定。
    phase_no / total_phases: バッチ 内 工程 番号 (1..7) と 総数。Web UI の 工程 インジケータ が 使用。
    batch_idx: どの バッチ の 工程 か を 示す (バッチ 切替 時 の インジケータ リセット 用)。

    v1.7.16: Notifies the current processing phase to the Web UI / GUI (per-batch step indicator).
    phase/title/phase_no/total_phases/batch_idx are passed through to the UI step indicator.
    """
    try:
        from pixel_sync.core.util import publish_phase as _pp
        _pp(phase=phase, detail=detail, title=title,
            phase_no=phase_no, total_phases=total_phases, batch_idx=batch_idx)
    except Exception:
        pass


def _cfg_batch_counts():
    """
    モジュール ロード 時点 の スナップショット では なく 「呼び出し 時点 の settings 最新 値」 を 返す。
    GUI / config_ui.json 経由 で ユーザー が 10GB 等 に 動的 変更 し た 場合 でも 正しく 反映 する ため の 対策。
    """
    cnt = int(getattr(_settings_module, "MAX_FILES_PER_BATCH", 30) or 30)
    byt = int(getattr(_settings_module, "MAX_BYTES_PER_BATCH", 2 * (1024 ** 3)) or 0)
    return max(1, cnt), max(0, byt)


class Uploader:

    def __init__(self, db: Database):

        self.db = db
        self.worker = UploadWorker()

    @staticmethod
    def _safe_size(file_path):
        try:
            p = Path(file_path)
            if p.is_file():
                return p.stat().st_size
        except OSError:
            pass
        return 0

    @staticmethod
    def _normalize(files):
        normalized = []
        for item in files:
            if isinstance(item, (tuple, list)):
                if len(item) >= 2:
                    p, fn = str(item[0]), str(item[1])
                else:
                    p = str(item[0])
                    fn = Path(p).name
            else:
                p = str(item)
                fn = Path(p).name
            normalized.append((p, fn))
        return normalized

    def print_progress(self):

        remain_files = self.db.get_remaining_count()
        remain_bytes = self.db.get_remaining_bytes()

        speed = self.db.get_average_bytes_per_second()
        eta = self.db.get_eta_seconds()

        print()
        print("=" * 60)
        print("Progress")
        print("=" * 60)

        print(tr("upl.remain_files").format(n=remain_files))
        print(tr("upl.remain_bytes").format(tb=remain_bytes / 1024**4))

        if speed > 0:
            print(tr("upl.avg_speed").format(mb=speed / 1024**2))
        else:
            print(tr("upl.avg_speed_na"))

        if eta:
            print(tr("upl.remain_time").format(td=timedelta(seconds=eta)))
        else:
            print(tr("upl.remain_time_na"))


    @staticmethod
    def _build_batches(files, max_count, max_bytes):
        """
        バッチ分割。settings.BATCH_STRATEGY に従い 排他動作:
          count  : ファイル数だけで切る (容量無視)
          bytes  : 容量だけで切る (件数 実質 上限無し)
          hybrid : 旧来 OR 条件 (件数/容量 どちらか先到着 で切替)
        """
        batches = []
        if not files:
            return batches
        try:
            from pixel_sync.core import settings as _set
            strategy = str(getattr(_set, "BATCH_STRATEGY", "count") or "count").strip().lower()
        except Exception:
            strategy = "count"
        cur = []
        cur_bytes = 0
        safe_max_count = max(1, int(max_count or 1))
        safe_max_bytes = int(max_bytes or 0)
        if strategy == "count":
            safe_max_bytes = 0
        elif strategy == "bytes":
            safe_max_count = 1_000_000_000
        for f in files:
            p = f[0] if isinstance(f, (tuple, list)) else f
            s = Uploader._safe_size(p)
            count_full = len(cur) >= safe_max_count
            size_full = (safe_max_bytes > 0) and (cur and (cur_bytes + s > safe_max_bytes))
            if cur and (count_full or size_full):
                batches.append(cur)
                cur = []
                cur_bytes = 0
            cur.append(f)
            cur_bytes += s
        if cur:
            batches.append(cur)
        return batches

    def upload_files(self, files):

        if not files:
            print(tr("upl.info.nothing"))
            return

        files = self._normalize(files)

        pixel_files = self.worker.camera_files()

        total = len(files)

        success = 0
        skipped = 0
        failed = 0

        print()
        print("=" * 60)
        print(tr("upload.start"))
        print("=" * 60)
        # コンソール 見出し と 同じ 文言 を Web UI の フェーズ 表示 へ 通知
        # Send the same console heading text to the Web UI phase display.
        _publish_phase("", "", title=tr("upload.start"))

        _MAX_FILES_PER_BATCH, _MAX_BYTES_PER_BATCH = _cfg_batch_counts()
        batches = self._build_batches(files, _MAX_FILES_PER_BATCH, _MAX_BYTES_PER_BATCH)

        # ====================================================================
        # v1.7.9 共通 ヘルパー: 強力 ADB 再接続 (Pre1 / Pre0 / 各 Phase 開始 前 に 必ず 呼び出し)
        # ====================================================================
        # ====================================================================
        # v1.7.9 shared helper: strong ADB reconnect
        # (always called before Pre1 / Pre0 / each phase start)
        # ====================================================================
        def _ensure_adb_robust(label: str = "", publish_unrecoverable: bool = True):
            try:
                import pixel_sync.core.settings as _ens_s
                policy = str(getattr(_ens_s, "ADB_FAILURE_POLICY", "pause_dialog") or "pause_dialog").strip().lower()
            except Exception:
                policy = "pause_dialog"
            try:
                ok = bool(self.worker.pixel.ensure_adb_alive(
                    aggressive=True,
                    shell_probe=True,
                    publish_unrecoverable=bool(publish_unrecoverable and policy == "pause_dialog"),
                ))
            except Exception:
                ok = False
            return ok

        # ====================================================================
        # v1.7.18: バッテリー監視 + ADB 切断復旧ゲート (長時間 評価 向け 安全 機構)
        #   - 低残量 (かつ 非充電) なら 充電 / 回復 まで 待機 (上限 あり)
        #   - デバイス 不在 なら 最大 ADB_RECOVERY_WAIT_SEC 秒 粘って 再接続
        #   - どちらも 上限 超過 / 復旧 不能 なら この バッチ の FAILED を PENDING に
        #     戻し て 安全 停止 (次回 実行 で 自動 再送)
        # v1.7.18: Battery monitoring + ADB disconnect recovery gate
        #   - Pause while the battery is low (and not charging), up to a limit
        #   - On device absence, retry reconnection for up to ADB_RECOVERY_WAIT_SEC
        #   - On limit/timeout, revert this batch's FAILED rows to PENDING and stop safely
        # ====================================================================
        _bat_state = {"last_check": 0.0}

        def _safe_int(v, default):
            try:
                return int(v)
            except Exception:
                return default

        def _safe_stop_batch(batch, message: str):
            """ADB 切断 等 で 続行 不能 な とき 、この バッチ の FAILED を PENDING に 戻し て 停止 要求 する 。
            Revert this batch's FAILED rows to PENDING and request a stop when unable to continue."""
            _reverted = 0
            try:
                _reverted = int(self.db.revert_phone_transfer_failed_to_pending(
                    [p for (p, _n) in batch]) or 0)
                self.db.commit()
            except Exception:
                _reverted = 0
            try:
                from pixel_sync.core.logger import Logger as _StopLg2
                _StopLg2.error(f"{message} (PENDINGに戻した失敗件数={_reverted})")
            except Exception:
                pass
            try:
                _control.request_stop_now()
            except Exception:
                pass

        def _battery_gate(force: bool = False):
            """低残量 (かつ 非充電) の とき 充電 / 残量 回復 まで 待機 する 。
            戻り値: "OK" (続行) / "STOPPED" (ユーザー 停止) / "TIMEOUT" (待機 上限 超過)。
            Wait until charging / recovery when the battery is low (and not charging).
            Returns "OK" / "STOPPED" / "TIMEOUT"."""
            try:
                interval = float(getattr(_settings_module, "BATTERY_CHECK_INTERVAL_SEC", 600) or 0)
            except Exception:
                interval = 600.0
            _now = time.time()
            if not force and interval > 0 and (_now - _bat_state["last_check"]) < interval:
                return "OK"
            _bat_state["last_check"] = _now

            thr = _safe_int(getattr(_settings_module, "BATTERY_LOW_THRESHOLD_PCT", 20), 20)
            try:
                wait_max = float(getattr(_settings_module, "BATTERY_WAIT_MAX_SEC", 3600) or 0)
            except Exception:
                wait_max = 3600.0

            def _read_health():
                try:
                    from pixel_sync.device.adb import ADB as _ADB_h
                    return _ADB_h.get_device_health() or {}
                except Exception:
                    return {}

            _h = _read_health()
            _lvl = _h.get("battery_level")
            _chg = _h.get("battery_charging")
            if _lvl is None:
                return "OK"
            _lvl_i = _safe_int(_lvl, -1)
            if _lvl_i < 0:
                return "OK"
            if _lvl_i >= thr or _chg is True:
                return "OK"

            try:
                from pixel_sync.core.logger import Logger as _BatLg
                _BatLg.warning(tr("log.flow.battery_low").format(level=_lvl_i, thr=thr))
            except Exception:
                pass

            _waited = 0.0
            _poll = 15.0
            while True:
                if _control.stop_now_requested():
                    return "STOPPED"
                _remain = max(0, int(wait_max - _waited)) if wait_max > 0 else 0
                _publish_phase(
                    "BATTERY_WAIT",
                    tr("log.flow.battery_wait").format(level=_lvl_i, thr=thr, remain=_remain),
                )
                time.sleep(_poll)
                _waited += _poll
                _h = _read_health()
                _lvl2 = _h.get("battery_level")
                _chg2 = _h.get("battery_charging")
                _l2 = _safe_int(_lvl2, -1)
                if _chg2 is True or (_l2 >= 0 and _l2 >= thr):
                    try:
                        from pixel_sync.core.logger import Logger as _BatOkLg
                        _BatOkLg.success(tr("log.flow.battery_ok").format(
                            level=(_l2 if _l2 >= 0 else _lvl_i)))
                    except Exception:
                        pass
                    return "OK"
                if _l2 >= 0:
                    _lvl_i = _l2
                if wait_max > 0 and _waited >= wait_max:
                    try:
                        from pixel_sync.core.logger import Logger as _BatNgLg
                        _BatNgLg.error(tr("log.flow.battery_timeout").format(min=round(wait_max / 60)))
                    except Exception:
                        pass
                    return "TIMEOUT"

        def _adb_recovery_gate(label: str = ""):
            """デバイス 不在 の とき 、最大 ADB_RECOVERY_WAIT_SEC 秒 aggressive 再接続 を 試みる 。
            戻り値: "OK" / "STOPPED" / "UNRECOVERABLE"。
            When the device is absent, retry aggressive reconnection for up to
            ADB_RECOVERY_WAIT_SEC seconds. Returns "OK" / "STOPPED" / "UNRECOVERABLE"."""
            try:
                from pixel_sync.device.adb import ADB as _ADB_g
                if _ADB_g.has_device(check_offline_too=False):
                    return "OK"
            except Exception:
                pass
            try:
                wait_max = float(getattr(_settings_module, "ADB_RECOVERY_WAIT_SEC", 300) or 300)
            except Exception:
                wait_max = 300.0
            try:
                from pixel_sync.core.logger import Logger as _GateLg
                _GateLg.warning(tr("log.flow.adb_gate_start").format(
                    min=round(wait_max / 60), label=label))
            except Exception:
                pass
            _t0 = time.time()
            _step = 5.0
            while True:
                if _control.stop_now_requested():
                    return "STOPPED"
                _remain = max(0, int(wait_max - (time.time() - _t0)))
                _publish_phase("ADB_RECONNECT", tr("log.flow.adb_gate_wait").format(remain=_remain))
                if (time.time() - _t0) >= wait_max:
                    break
                try:
                    _ok = bool(self.worker.pixel.ensure_adb_alive(
                        aggressive=True, shell_probe=True, publish_unrecoverable=False))
                except Exception:
                    _ok = False
                if _ok:
                    try:
                        from pixel_sync.core.logger import Logger as _GateOkLg
                        _GateOkLg.success(tr("log.flow.adb_gate_ok").format(
                            sec=round(time.time() - _t0, 1)))
                    except Exception:
                        pass
                    try:
                        _events.publish(_events.ADB_DEVICE_RECOVERED,
                                        {"phase": "recovery_gate", "label": label})
                    except Exception:
                        pass
                    return "OK"
                time.sleep(_step)
            _fail_msg = tr("log.flow.adb_gate_fail").format(min=round(wait_max / 60), label=label)
            try:
                _events.publish(_events.ADB_DEVICE_UNRECOVERABLE,
                                {"detail": _fail_msg, "phase": "recovery_gate"})
            except Exception:
                pass
            try:
                from pixel_sync.core.logger import Logger as _GateNgLg
                _GateNgLg.error(_fail_msg)
            except Exception:
                pass
            return "UNRECOVERABLE"

        # ====================================================================
        # (Pre-1) ★ v1.7.10 STORAGE3: Pixel 自動 GC Phase 強化版
        #   実際 に Pixel 上 rm 成功 し た ファイル だけ を DB mark_phone_delete_done_bulk に 渡す 。
        #   (旧: 全件 DONE 化 → 失敗 分 が 次回 GC 対象 外 で 永久 残留 する バグ)
        # ====================================================================
        # (Pre-1) v1.7.10 STORAGE3: Enhanced Pixel auto-GC phase
        #   Pass to DB mark_phone_delete_done_bulk only files actually rm'd on Pixel.
        #   (Old: marking all DONE left failed files permanently out of next GC scope)
        # ====================================================================
        try:
            _ensure_adb_robust("Pre-1 GC start", publish_unrecoverable=True)
        except Exception:
            pass
        try:
            from pixel_sync.core.logger import Logger as _GcLg
            _gc_list = self.db.get_phone_pending_delete_files() or []
            try:
                _gc_before_free = float(self.worker.pixel.get_storage_root_free_gb() or 0.0)
            except Exception:
                _gc_before_free = 0.0
            if _gc_list:
                _GcLg.info(
                    tr("log.flow.gc_start").format(pending=len(_gc_list), free=_gc_before_free)
                )
                _publish_phase("GC", tr("log.flow.gc_phase").format(n=len(_gc_list)))
                try:
                    import time as _gc_t0
                    _t0 = _gc_t0.time()
                    _gc_deleted_fnames = self.worker.delete_uploaded_files(
                        [fn for (_p, fn) in _gc_list]
                    ) or []
                    _gc_deleted_set = set(_gc_deleted_fnames)
                    _gc_dt = max(0.0, _gc_t0.time() - _t0)
                    _gc_updated_db = 0
                    try:
                        _gc_ok_paths = [
                            p for (p, fn) in _gc_list if fn in _gc_deleted_set
                        ]
                        if _gc_ok_paths:
                            _gc_updated_db = int(self.db.mark_phone_delete_done_bulk(
                                _gc_ok_paths
                            ) or 0)
                            self.db.commit()
                    except Exception:
                        pass
                    try:
                        self.worker.pixel.run_media_scanner_and_wait(3.0)
                    except Exception:
                        pass
                    try:
                        _gc_after_free = float(self.worker.pixel.get_storage_root_free_gb() or 0.0)
                    except Exception:
                        _gc_after_free = _gc_before_free
                    _delta_free = max(0.0, _gc_after_free - _gc_before_free)
                    _gc_total = int(len(_gc_list) or 0)
                    _gc_ok = int(len(_gc_deleted_fnames) or 0)
                    _gc_rate = (float(_gc_ok) / float(_gc_total)) if _gc_total > 0 else 1.0
                    print(
                        tr("log.flow.gc_done").format(ok=_gc_ok, total=_gc_total, db=_gc_updated_db, dt=_gc_dt, fb=_gc_before_free, fa=_gc_after_free, df=_delta_free)
                    )
                    _GcLg.success(
                        tr("log.flow.gc_ok_log").format(ok=_gc_ok, total=_gc_total, db=_gc_updated_db, dt=_gc_dt, fb=_gc_before_free, fa=_gc_after_free, df=_delta_free)
                    )
                    if _gc_total > 0 and _gc_rate < 1.0:
                        _GcLg.warning(
                            tr("log.flow.gc_warn").format(rate=round(_gc_rate * 100, 1), ok=_gc_ok, total=_gc_total, fail=_gc_total - _gc_ok)
                        )
                    try:
                        _events.publish(_events.PIXEL_STORAGE_RECOVERED, {
                            "phase": "pre1_gc",
                            "deleted": _gc_ok,
                            "gc_files": _gc_total,
                            "free_before": round(_gc_before_free, 2),
                            "free_after": round(_gc_after_free, 2),
                            "delta_gb": round(_delta_free, 2),
                        })
                    except Exception:
                        pass
                except Exception as _gc_ex:
                    import traceback as _gc_tb
                    _GcLg.error(
                        tr("log.flow.gc_failed").format(exc_type=type(_gc_ex).__name__, exc=_gc_ex, tb=_gc_tb.format_exc())
                    )
        except Exception:
            pass

        batch_index = 0

        for batch in batches:

            # v1.7.15: 停止 要求 (「今回のバッチで終了」 / 即時停止) → 直前 の バッチ まで を
            #   完了 済み として バッチ ループ を 抜け る (次回 実行 で 残り を 再処理)。
            # v1.7.15: Stop request ("stop after this batch" / immediate stop) -> exit the loop,
            #   treating previous batches as done (remaining work is reprocessed on the next run).
            if _control.stop_after_batch_requested() or _control.stop_now_requested():
                print()
                print(tr("log.flow.stop_batch_line1"))
                print(tr("log.flow.stop_batch_line2"))
                try:
                    from pixel_sync.core.logger import Logger as _StopLg
                    _StopLg.warning(
                        tr("log.flow.stop_batch_log").format(
                            batch=batch_index, total=len(batches),
                            success=success, skipped=skipped, failed=failed)
                    )
                except Exception:
                    pass
                break

            batch_started = datetime.now()
            batch_started_ts = time.time()

            batch_index += 1

            self.print_progress()

            try:
                remain_files = self.db.get_remaining_count()
                try:
                    total_cnt = int(self.db.count() or 0)
                    done_approx = max(0, total_cnt - int(remain_files))
                except Exception:
                    done_approx = int((batch_index - 1) * _MAX_FILES_PER_BATCH)
                cur_file = ""
                if batch:
                    try:
                        cur_file = str(batch[-1][1] if len(batch[-1]) >= 2 else batch[-1][0])
                    except Exception:
                        cur_file = ""
                state = {
                    "batch_idx": batch_index,
                    "total_batches": len(batches),
                    "remain_files": remain_files,
                    "done_files": done_approx,
                    "speed_bps": self.db.get_average_bytes_per_second(),
                    "eta_seconds": self.db.get_eta_seconds(),
                    "current_file": cur_file,
                }
                _events.publish(_events.UPLOAD_PROGRESS_TICK, state)
            except Exception:
                pass

            print()
            print(
                f"========== "
                f"BATCH {batch_index}/{len(batches)} "
                f"=========="
            )

            # v1.7.18: バッチ 開始 前 に バッテリー 残量 を 必ず 確認 (低残量 なら 充電 待ち)。
            # v1.7.18: Always check the battery before a batch (wait for charging when low).
            _bg_batch = _battery_gate(force=True)
            if _bg_batch == "TIMEOUT":
                _safe_stop_batch(batch, tr("log.flow.battery_stop"))
                break
            if _bg_batch == "STOPPED":
                break

            # ---- (Pre-0) ★ v1.7.7 新規: Pixel 空き容量 チェック
            #       バッチ 合計 サイズ を 計算 し 、 Pixel 空き容量 - マージン を 下回っ て
            #       い ない か 確認。 最大 3 回 まで 10秒 間隔 で 再取得 し 、 解消 すれ ば 続行
            #       (Pre-1 GC の 反映 待ち や ユーザー が Pixel 側 を 手動 削除 する 猶予)。
            # ---- (Pre-0) v1.7.7: Pixel free-space check
            #       Sum batch size and verify it does not fall below Pixel free space minus margin.
            #       Retry up to 3 times at 10s intervals; continue if resolved
            #       (grace period for Pre-1 GC to apply or user to delete manually on Pixel).
            try:
                _ensure_adb_robust(f"Pre-0 BATCH {batch_index}", publish_unrecoverable=True)
            except Exception:
                pass
            # 工程インジケータ: 工程1「容量確保」を点灯 (通常は一瞬で通過 / 不足時のみ滞留)
            # Step indicator: light up step 1 "ensure capacity" (usually instant, lingers only when short)
            _publish_phase("CAPACITY", tr("log.flow.phase_capacity").format(batch=batch_index), phase_no=1, total_phases=7, batch_idx=batch_index)
            _batch_total_bytes = 0
            for (file_path, _fn) in batch:
                _batch_total_bytes += Uploader._safe_size(file_path)
            _need_gb = round(_batch_total_bytes / 1024**3, 3)
            _margin_gb = float(getattr(__import__("pixel_sync.core.settings", fromlist=["_"]), "PIXEL_STORAGE_MARGIN_GB", 4.0))
            _STORAGE_RETRY_MAX = 3
            _STORAGE_RETRY_WAIT = 10
            _storage_ok = False
            _last_free_gb = 0.0
            for _storage_retry_i in range(_STORAGE_RETRY_MAX):
                try:
                    _free_gb = float(self.worker.pixel.get_storage_root_free_gb() or 0.0)
                except Exception:
                    _free_gb = 0.0
                _last_free_gb = _free_gb
                if _need_gb <= 0 or _free_gb <= 0 or (_need_gb + _margin_gb) <= _free_gb:
                    _storage_ok = True
                    break
                if _storage_retry_i < _STORAGE_RETRY_MAX - 1:
                    _shortage = max(0.0, (_need_gb + _margin_gb) - _free_gb)
                    try:
                        from pixel_sync.core.logger import Logger as _WtLg
                        _WtLg.info(
                            tr("log.flow.pre0_wait_log").format(batch=batch_index, total=len(batches), shortage=_shortage, n=_storage_retry_i + 1, max=_STORAGE_RETRY_MAX - 1, wait=_STORAGE_RETRY_WAIT)
                        )
                    except Exception:
                        pass
                    print(
                        tr("log.flow.pre0_wait").format(shortage=_shortage, wait=_STORAGE_RETRY_WAIT)
                    )
                    try:
                        import time as _wt_t
                        _wt_t.sleep(_STORAGE_RETRY_WAIT)
                    except Exception:
                        pass
            _free_gb = _last_free_gb
            if not _storage_ok and (_need_gb > 0 and _free_gb > 0 and (_need_gb + _margin_gb) > _free_gb):
                _AUTO_RECOVER_MAX = 3
                _recover_ok = False
                _recover_sequence_log_lines = []
                _publish_phase("RECOVER", tr("log.flow.phase_recover").format(batch=batch_index), phase_no=1, total_phases=7, batch_idx=batch_index)
                try:
                    from pixel_sync.core.logger import Logger as _AutoRcvLg
                    _Shortage0 = max(0.0, (_need_gb + _margin_gb) - _free_gb)
                    _AutoRcvLg.warning(
                        tr("log.flow.recover_seq_start").format(batch=batch_index, total=len(batches), need=_need_gb, margin=_margin_gb, free=_free_gb, shortage=_Shortage0, max=_AUTO_RECOVER_MAX)
                    )
                except Exception:
                    pass
                try:
                    print(
                        tr("log.flow.recover_seq_print").format(max=_AUTO_RECOVER_MAX)
                    )
                except Exception:
                    pass
                for _recover_i in range(_AUTO_RECOVER_MAX):
                    try:
                        _seq_num = _recover_i + 1
                        try:
                            from pixel_sync.core.logger import Logger as _RcLg
                            _RcLg.info(
                                tr("log.flow.recover_s1_log").format(batch=batch_index, total=len(batches), seq=_seq_num, max=_AUTO_RECOVER_MAX)
                            )
                        except Exception:
                            pass
                        try:
                            print(
                                tr("log.flow.recover_s1").format(seq=_seq_num, max=_AUTO_RECOVER_MAX)
                            )
                        except Exception:
                            pass
                        try:
                            _ensure_adb_robust(f"Pre-0 recover-S1 B{batch_index}", publish_unrecoverable=True)
                        except Exception:
                            pass
                        try:
                            _gc_rerun_list = self.db.get_phone_pending_delete_files() or []
                        except Exception:
                            _gc_rerun_list = []
                        _gc_rerun_deleted_fnames = set()
                        try:
                            if _gc_rerun_list:
                                _rerun_del = self.worker.delete_uploaded_files(
                                    [fn for (_p, fn) in _gc_rerun_list]
                                ) or []
                                _gc_rerun_deleted_fnames = set(_rerun_del)
                                try:
                                    _paths_to_mark = [
                                        p for (p, fn) in _gc_rerun_list if fn in _gc_rerun_deleted_fnames
                                    ]
                                    if _paths_to_mark:
                                        _db_m = int(self.db.mark_phone_delete_done_bulk(_paths_to_mark) or 0)
                                        self.db.commit()
                                        try:
                                            from pixel_sync.core.logger import Logger as _G1Lg
                                            _G1Lg.info(
                                                tr("log.flow.recover_s1_done").format(seq=_seq_num, max=_AUTO_RECOVER_MAX, deleted=len(_rerun_del), total=len(_gc_rerun_list), db=_db_m)
                                            )
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                        except Exception as _e1:
                            try:
                                from pixel_sync.core.logger import Logger as _ErrLg
                                _ErrLg.warning(tr("log.flow.recover_s1_err").format(seq=_seq_num, max=_AUTO_RECOVER_MAX, exc_type=type(_e1).__name__, exc=_e1))
                            except Exception:
                                pass
                        try:
                            self.worker.pixel.run_media_scanner_and_wait(3.0)
                        except Exception:
                            pass
                        try:
                            _f_s1 = float(self.worker.pixel.get_storage_root_free_gb() or 0.0)
                        except Exception:
                            _f_s1 = _free_gb
                        try:
                            print(
                                tr("log.flow.recover_s1_result").format(f1=_free_gb, f2=_f_s1, d=max(0., _f_s1 - _free_gb))
                            )
                        except Exception:
                            pass
                        if (_need_gb + _margin_gb) <= _f_s1:
                            try:
                                _free_gb = _f_s1
                                _storage_ok = True
                                _recover_ok = True
                                try:
                                    from pixel_sync.core.logger import Logger as _OkLg
                                    _OkLg.success(
                                        tr("log.flow.recover_s1_ok").format(batch=batch_index, total=len(batches), seq=_seq_num, max=_AUTO_RECOVER_MAX, free=_free_gb, need=_need_gb + _margin_gb)
                                    )
                                except Exception:
                                    pass
                                break
                            except Exception:
                                _storage_ok = True
                                _recover_ok = True
                                break
                        # v1.8.0: 旧 Step2 は /sdcard/DCIM を 丸 ごと 削除 し て い た が 、 個人 の 写真 を
                        # 巻き込む 危険 が あ る ため 撤廃 し た 。 Step1 で NAS2GP が 転送 し た ファイル は
                        # 既に 削除 済み な ので 、 次 は Step2(端末 再起動) へ 進む 。
                        # v1.8.0: the old Step2 wiped the whole /sdcard/DCIM, which risked deleting
                        # personal photos, so it was removed. Step1 already deleted NAS2GP-transferred
                        # files, so proceed to Step2 (device reboot).
                        _f_s2 = _f_s1
                        try:
                            from pixel_sync.core.logger import Logger as _S3Lg
                            _S3Lg.info(
                                tr("log.flow.recover_s3_log").format(batch=batch_index, total=len(batches), seq=_seq_num, max=_AUTO_RECOVER_MAX)
                            )
                        except Exception:
                            pass
                        try:
                            print(
                                tr("log.flow.recover_s3").format(seq=_seq_num, max=_AUTO_RECOVER_MAX)
                            )
                        except Exception:
                            pass
                        _reboot_ok = False
                        try:
                            _reboot_ok = bool(self.worker.pixel.reboot_device(wait_seconds_after_reboot=45.0))
                        except Exception as _e3:
                            try:
                                from pixel_sync.core.logger import Logger as _E3Lg
                                _E3Lg.warning(tr("log.flow.recover_s3_err").format(seq=_seq_num, max=_AUTO_RECOVER_MAX, exc_type=type(_e3).__name__, exc=_e3))
                            except Exception:
                                pass
                        try:
                            _ensure_adb_robust(f"Pre-0 recover-S3-after B{batch_index}", publish_unrecoverable=True)
                        except Exception:
                            pass
                        try:
                            self.worker.pixel.run_media_scanner_and_wait(4.0)
                        except Exception:
                            pass
                        try:
                            _f_s3 = float(self.worker.pixel.get_storage_root_free_gb() or 0.0)
                        except Exception:
                            _f_s3 = _f_s2
                        try:
                            print(
                                tr("log.flow.recover_s3_result").format(f1=_f_s2, f2=_f_s3, ok=_reboot_ok)
                            )
                        except Exception:
                            pass
                        if (_need_gb + _margin_gb) <= _f_s3:
                            _free_gb = _f_s3
                            _storage_ok = True
                            _recover_ok = True
                            try:
                                from pixel_sync.core.logger import Logger as _S3OkLg
                                _S3OkLg.success(
                                    tr("log.flow.recover_s3_ok").format(batch=batch_index, total=len(batches), seq=_seq_num, max=_AUTO_RECOVER_MAX, free=_free_gb)
                                )
                            except Exception:
                                pass
                            break
                        try:
                            from pixel_sync.core.logger import Logger as _RetryLg
                            _RetryLg.info(
                                tr("log.flow.recover_retry").format(batch=batch_index, total=len(batches), seq=_seq_num, max=_AUTO_RECOVER_MAX, free=_f_s3)
                            )
                        except Exception:
                            pass
                        try:
                            import time as _wt_sleep
                            _wt_sleep.sleep(4)
                        except Exception:
                            pass
                    except Exception as _loop_e:
                        try:
                            from pixel_sync.core.logger import Logger as _LoopErrLg
                            _LoopErrLg.warning(
                                tr("log.flow.recover_loop_err").format(batch=batch_index, total=len(batches), exc_type=type(_loop_e).__name__, exc=_loop_e)
                            )
                        except Exception:
                            pass
                        try:
                            import time as _e_sl
                            _e_sl.sleep(3)
                        except Exception:
                            pass
                if not _storage_ok:
                    try:
                        from pixel_sync.core.logger import Logger as _FailLg
                        _FailLg.error(
                            tr("log.flow.pre0_fatal_log").format(batch=batch_index, total=len(batches), max=_AUTO_RECOVER_MAX, free=_free_gb, need=_need_gb, margin=_margin_gb)
                        )
                    except Exception:
                        pass
                    try:
                        print(
                            tr("log.flow.pre0_fatal")
                        )
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"PIXEL_STORAGE_INSUFFICIENT_FATAL: batch {batch_index}/{len(batches)} "
                        f"need={_need_gb:.2f}GB + margin={_margin_gb:.1f}GB > final_free={_free_gb:.2f}GB "
                        f"(auto-recovery {_AUTO_RECOVER_MAX} loops exhausted)"
                    )
                try:
                    _events.publish(_events.PIXEL_STORAGE_RECOVERED, {
                        "phase": "pre0_auto_recover",
                        "loops": int(_recover_i + 1 if '_recover_i' in dir() else _AUTO_RECOVER_MAX),
                        "required_gb": round(_need_gb, 2),
                        "margin_gb": float(_margin_gb),
                        "free_before": round(_last_free_gb, 2),
                        "free_after": round(_free_gb, 2),
                    })
                except Exception:
                    pass
                try:
                    from pixel_sync.core.logger import Logger as _AfterLg
                    _AfterLg.info(
                        tr("log.flow.pre0_ok").format(batch=batch_index, total=len(batches))
                    )
                except Exception:
                    pass
                pass
            if False:
                try:
                    from pixel_sync.core.logger import Logger as _StrLg
                    _msg = (
                        tr("log.flow.pre0_insufficient").format(batch=batch_index, total=len(batches), need=_need_gb, margin=_margin_gb, free=_free_gb)
                    )
                    _StrLg.warning(_msg)
                except Exception:
                    pass
                try:
                    _events.publish(
                        _events.PIXEL_STORAGE_INSUFFICIENT,
                        {
                            "batch_index": batch_index,
                            "total_batches": len(batches),
                            "required_gb": round(_need_gb, 2),
                            "free_gb": round(_free_gb, 2),
                            "margin_gb": float(_margin_gb),
                            "batch_files": len(batch),
                            "batch_bytes": int(_batch_total_bytes),
                        },
                    )
                except Exception:
                    pass
                try:
                    print(tr("log.flow.pre0_skip").format(batch=batch_index, need=_need_gb, margin=_margin_gb, free=_free_gb))
                    from pixel_sync.core.logger import Logger as _SkipLg
                    _SkipLg.info(
                        f"[BATCH {batch_index}/{len(batches)}] [Pre-0 SKIP] storage insufficient (need={_need_gb:.2f}GB + margin={_margin_gb:.1f}GB > free={_free_gb:.2f}GB). Skip and wait user deletion on the device."
                    )
                except Exception:
                    pass
                try:
                    state = {
                        "batch_idx": batch_index,
                        "total_batches": len(batches),
                        "remain_files": int(remain_files) if 'remain_files' in dir() else 0,
                        "done_files": int(done_approx) if 'done_approx' in dir() else 0,
                        "speed_bps": 0,
                        "eta_seconds": 0,
                        "current_file": "[SKIP] storage insufficient",
                    }
                    _events.publish(_events.UPLOAD_PROGRESS_TICK, state)
                except Exception:
                    pass
                continue

            # ========================================================================
            # ★ v1.7.7 大幅 変更: 1バッチ の フェーズ 順序 を 厳格 化 (ユーザー 要望 反映)
            #   Phase 1/6 Google Photos STOP  (全バッチ 必須 / CYCLE_GP フラグ に 依存 せず 常に実施)
            #   Phase 2/6 NAS → Pixel ファイル 転送
            #   Phase 3/6 Media Scanner broadcast (Pixel OS に 新ファイル 認識 させる)
            #   Phase 4/6 Google Photos START + scroll top + CYCLE_GP_DELAY 待機 (全バッチ 必須)
            #   Phase 5/6 Google Photos backup 完了 待機 → 完了確認
            #   Phase 6/6 Pixel 側 の アップロード完了 ファイル 削除 (backup_ok 時 のみ)
            # ========================================================================
            # ========================================================================
            # v1.7.7: strict per-batch phase order:
            #   1/6 GP STOP, 2/6 NAS->Pixel transfer, 3/6 media scanner broadcast,
            #   4/6 GP START + scroll top + delay, 5/6 wait for backup, 6/6 delete uploaded files.
            # ========================================================================

            # ---- Phase 1/6: Google Photos force-stop
            try:
                _ensure_adb_robust(f"Phase1 BATCH {batch_index}", publish_unrecoverable=True)
            except Exception:
                pass
            try:
                from pixel_sync.core import settings as _up_s
                delay_after = int(getattr(_up_s, "CYCLE_GP_DELAY_AFTER_START_SECONDS", 5) or 5)
                from pixel_sync.core.logger import Logger as _FLLg
                _FLLg.info(
                    tr("log.flow.phase1_gpstop_log").format(batch=batch_index, total=len(batches), n=len(batch))
                )
            except Exception:
                pass
            print(tr("log.flow.phase1_gpstop"))
            print(tr("log.flow.phase1_gpstop_step"))
            _publish_phase("GP_STOP", tr("log.flow.phase_gpstop").format(batch=batch_index), phase_no=2, total_phases=7, batch_idx=batch_index)
            try:
                self.worker.pixel.stop_google_photos()
            except Exception as e:
                import traceback as _tb
                from pixel_sync.core.logger import Logger as _LGPstop
                _LGPstop.warning(f"Phase1 Google Photos stop failed: {type(e).__name__}: {e}\n{_tb.format_exc()}")

            # ---- Phase 2/6: NAS → Pixel 転送
            # ---- Phase 2/6: NAS -> Pixel transfer
            try:
                _ensure_adb_robust(f"Phase2 BATCH {batch_index}", publish_unrecoverable=True)
            except Exception:
                pass
            try:
                from pixel_sync.core.logger import Logger as _FLLg2
                _FLLg2.info(
                    tr("log.flow.phase2_start_log").format(batch=batch_index, total=len(batches), n=len(batch), gb=round(_batch_total_bytes/1024**3,3))
                )
            except Exception:
                pass
            _publish_phase("UPLOAD", tr("log.flow.phase_upload").format(batch=batch_index, n=len(batch)), phase_no=3, total_phases=7, batch_idx=batch_index)
            print(tr("log.flow.phase2_start").format(n=len(batch), gb=round(_need_gb,3)))

            uploaded_files = []
            recovery_count = 0
            backup_ok = False

            for (file_path, filename) in batch:

                # v1.7.15: 即時 停止 要求 → この バッチ の 残り ファイル 転送 を 中断 する。
                #   送信 済み ファイル の GP バックアップ 待ち / 削除 は 安全 の ため 継続 する。
                # v1.7.15: Immediate stop request -> abort remaining file transfers in this batch.
                #   GP backup wait / deletion for already-sent files continues for safety.
                if _control.stop_now_requested():
                    print()
                    print(tr("log.flow.stop_now").format(n=len(batch)))
                    try:
                        from pixel_sync.core.logger import Logger as _StopNowLg
                        _StopNowLg.warning(
                            tr("log.flow.stop_now_log").format(batch=batch_index, total=len(batches))
                        )
                    except Exception:
                        pass
                    break

                # v1.7.18: 転送 前 に バッテリー 残量 を 定期 確認 (低残量 かつ 非充電 なら 充電 待ち)。
                # v1.7.18: Periodically check the battery before each transfer
                #   (wait for charging when low and not charging).
                _bg_file = _battery_gate(force=False)
                if _bg_file == "TIMEOUT":
                    _safe_stop_batch(batch, tr("log.flow.battery_stop"))
                    break
                if _bg_file == "STOPPED":
                    break

                result, resolved_path, resolved_name = (
                    self.worker.upload(
                        file_path,
                        pixel_files,
                    )
                )

                file_path = resolved_path or file_path
                filename = resolved_name or filename

                if result == "SUCCESS":

                    self.db.update_phone_transfer_state(
                        file_path,
                        "DONE",
                    )

                    pixel_files.add(filename)

                    uploaded_files.append(
                        (file_path, filename)
                    )

                    success += 1

                elif result == "SKIP":

                    self.db.update_phone_transfer_state(
                        file_path,
                        "DONE",
                    )

                    skipped += 1

                else:
                    print(
                        f"{result:<7} "
                        f"{filename}",
                        flush=True,
                    )

                    self.db.update_phone_transfer_state(
                        file_path,
                        "FAILED",
                    )

                    failed += 1

                    # v1.7.18: 転送 失敗 時 だけ デバイス 在否 を 確認 する (毎 ファイル 確認 せ ず 軽量 化)。
                    #   不在 なら 最大 N 分 復旧 を 試み 、 復旧 不能 なら この バッチ の 失敗 分 を
                    #   PENDING に 戻し て 安全 停止 する 。 デバイス が 在る 場合 (= ADB 以外 の 失敗) は
                    #   1 回 の adb devices 確認 だけで 次 の ファイル へ 進む 。
                    # v1.7.18: Check device presence only on transfer failure (lighter than per-file checks).
                    #   If absent, try recovery for up to N min; if unrecoverable, revert this batch's
                    #   FAILED rows to PENDING and stop safely. If present (non-ADB failure), it costs
                    #   just one adb devices call and proceeds to the next file.
                    _rg_file = _adb_recovery_gate(f"BATCH {batch_index} {filename}")
                    if _rg_file == "UNRECOVERABLE":
                        _safe_stop_batch(batch, tr("log.flow.adb_stop"))
                        break
                    if _rg_file == "STOPPED":
                        break

                # v1.7.15: 成功/失敗/スキップ の 累計 を Web UI へ 逐次 反映 する
                #   (旧版 は 実行 終了 時 まで 0/0/0 の まま で 進行 状況 が 見え な かっ た)。
                # v1.7.15: Reflect running success/fail/skip totals to the Web UI incrementally
                #   (old versions stayed at 0/0/0 until completion, hiding progress).
                try:
                    _events.publish(_events.UPLOAD_PROGRESS_TICK, {
                        "batch_idx": batch_index,
                        "total_batches": len(batches),
                        "success": success,
                        "skipped": skipped,
                        "failed": failed,
                        "current_file": filename,
                    })
                except Exception:
                    pass
            self.db.commit()

            # v1.7.18: ADB 切断 による 安全 停止 の 場合 、以降 の ADB 依存 フェーズ
            #   (メディアスキャン / GP 操作 / バックアップ待ち / 削除) は 実行 不能 なので
            #   スキップ し て バッチ ループ を 抜け る (次回 実行 で 再開)。
            # v1.7.18: On a safe stop caused by an ADB disconnect, the remaining
            #   ADB-dependent phases are hopeless; skip them and exit the batch loop.
            if _control.stop_now_requested():
                try:
                    from pixel_sync.device.adb import ADB as _ADB_s
                    _dev_ok = bool(_ADB_s.has_device(check_offline_too=False))
                except Exception:
                    _dev_ok = False
                if not _dev_ok:
                    try:
                        from pixel_sync.core.logger import Logger as _SkipLg
                        _SkipLg.warning(tr("log.flow.adb_stop_skip_phases"))
                    except Exception:
                        pass
                    try:
                        print(tr("log.flow.adb_stop_skip_phases"))
                    except Exception:
                        pass
                    break

            # ---- Phase 3/6: Media scan
            try:
                from pixel_sync.core.logger import Logger as _MSLg
                if len(uploaded_files) > 0:
                    _MSLg.info(tr("log.flow.phase3_log").format(batch=batch_index, total=len(batches), n=len(uploaded_files)))
            except Exception:
                pass
            print()
            print("Media Scanner (Batch)")
            _publish_phase("MEDIA_SCAN", tr("log.flow.phase_mediascan").format(batch=batch_index), phase_no=4, total_phases=7, batch_idx=batch_index)
            names_only = [n for _, n in uploaded_files]
            self.worker.pixel.scan_media_batch(names_only)

            # ---- Phase 4/6: Google Photos START + scroll top + delay 待機 (全バッチ 強制実行)
            # ---- Phase 4/6: Google Photos START + scroll top + delay wait (forced for every batch)
            if len(uploaded_files) > 0:
                _publish_phase("GP_START", tr("log.flow.phase_gpstart").format(batch=batch_index), phase_no=5, total_phases=7, batch_idx=batch_index)
                try:
                    from pixel_sync.core import settings as _up_s2
                    delay_after = int(getattr(_up_s2, "CYCLE_GP_DELAY_AFTER_START_SECONDS", 5) or 5)
                    try:
                        from pixel_sync.core.logger import Logger as _FSLg
                        _FSLg.info(
                            tr("log.flow.phase4_log").format(batch=batch_index, total=len(batches), delay=delay_after)
                        )
                    except Exception:
                        pass
                    print(
                        tr("log.flow.phase4").format(delay=delay_after)
                    )
                    self.worker.pixel.start_google_photos()
                    time.sleep(max(0, int(delay_after)))
                    from pixel_sync.core.logger import Logger as _U3
                    _U3.success(
                        tr("log.flow.gp_cycle_ok_log").format(batch=batch_index, total=len(batches))
                    )
                    print(
                        tr("log.flow.gp_cycle_ok").format(delay=delay_after)
                    )
                except Exception as e:
                    import traceback as _tb2
                    from pixel_sync.core.logger import Logger as _L4
                    err = tr("log.flow.phase4_err").format(exc_type=type(e).__name__, exc=e)
                    print(f"  [Google Photos Cycle ERROR] {err}\n{_tb2.format_exc()}")
                    _L4.warning(err)

            # ---- Phase 5/6: Wait backup + backup 完了確認
            # ---- Phase 5/6: Wait backup + verify backup completion
            try:
                _ensure_adb_robust(f"Phase5 BATCH {batch_index}", publish_unrecoverable=True)
            except Exception:
                pass
            try:
                from pixel_sync.core.logger import Logger as _WBLg
                _WBLg.info(
                    tr("log.flow.phase5_log").format(batch=batch_index, total=len(batches), timeout=getattr(_up_s2 if '_up_s2' in dir() else None, 'BACKUP_TIMEOUT', 720))
                )
            except Exception:
                pass
            _publish_phase("WAIT_BACKUP", tr("log.flow.phase_waitbackup").format(batch=batch_index), phase_no=6, total_phases=7, batch_idx=batch_index)
            print()
            print(tr("backup.wait"))

            batch_status = "DONE"

            backup_started_ts = time.time()
            backup_result = self.worker.wait_backup_complete()
            if isinstance(backup_result, tuple) and len(backup_result) >= 2:
                backup_ok = bool(backup_result[0])
                recovery_count = int(backup_result[1])
            else:
                backup_ok = bool(backup_result)
                recovery_count = 0
            backup_seconds = max(0.0, time.time() - backup_started_ts)
            upload_seconds = max(0.0, backup_started_ts - batch_started_ts)

            # Phase 5/6 完了確認 ログ
            # Phase 5/6 completion confirmation log
            try:
                from pixel_sync.core.logger import Logger as _BkEnd
                if backup_ok:
                    _BkEnd.success(
                        tr("log.flow.phase5_ok").format(batch=batch_index, total=len(batches), rc=recovery_count, sec=round(backup_seconds,1))
                    )
                else:
                    _BkEnd.warning(
                        tr("log.flow.phase5_ng").format(batch=batch_index, total=len(batches), rc=recovery_count, sec=round(backup_seconds,1))
                    )
            except Exception:
                pass

            # Failsafe restart policy (従来 処理 維持)
            # Failsafe restart policy (keeps legacy behavior)
            need_restart = False
            restart_reason = ""
            if RESTART_PHOTOS_AFTER_PUSH:
                need_restart = True
                restart_reason = "RESTART_PHOTOS_AFTER_PUSH=True (legacy compat)"
            if recovery_count >= FAILSAFE_RECOVERY_THRESHOLD_PER_BATCH:
                need_restart = True
                if restart_reason: restart_reason += "; "
                restart_reason += (
                    f"recovery_count={recovery_count} >= "
                    f"FAILSAFE_RECOVERY_THRESHOLD={FAILSAFE_RECOVERY_THRESHOLD_PER_BATCH}"
                )
            if (not backup_ok) and FAILSAFE_RESTART_ON_BACKUP_TIMEOUT:
                need_restart = True
                if restart_reason: restart_reason += "; "
                restart_reason += "backup_ok=False + FAILSAFE_RESTART_ON_BACKUP_TIMEOUT=True"

            print()
            if need_restart:
                print(tr("upl.restart.reason_prefix").format(reason=restart_reason))
                self.worker.pixel.restart_google_photos()
            else:
                print(
                    tr("upl.skip.header").format(
                        bok=int(backup_ok),
                        rc=recovery_count,
                        threshold=FAILSAFE_RECOVERY_THRESHOLD_PER_BATCH,
                    )
                )
                print(tr("upl.skip.reason_no_kill"))

            # ---- Phase 6/6: Pixel 側 アップロード 完了 ファイル 削除 (backup_ok 時 のみ)
            # ---- Phase 6/6: Delete uploaded-complete files on Pixel (only when backup_ok)
            if backup_ok:

                try:
                    _ensure_adb_robust(f"Phase6 BATCH {batch_index}", publish_unrecoverable=True)
                except Exception:
                    pass

                try:
                    from pixel_sync.core.logger import Logger as _DelLg
                    _DelLg.info(
                        tr("log.flow.phase6_log").format(batch=batch_index, total=len(batches), n=len(uploaded_files))
                    )
                except Exception:
                    pass
                _publish_phase("DELETE", tr("log.flow.phase_delete").format(batch=batch_index, n=len(uploaded_files)), phase_no=7, total_phases=7, batch_idx=batch_index)
                print()
                print(tr("log.flow.phase6"))

                for file_path, _ in uploaded_files:
                    self.db.update_google_photos_state(file_path, "DONE")
                self.db.commit()

                deleted_files = self.worker.delete_uploaded_files(
                    [filename for _, filename in uploaded_files]
                )
                deleted_set = set(deleted_files)

                for file_path, filename in uploaded_files:
                    if filename in deleted_set:
                        self.db.update_phone_delete_state(file_path, "DONE")
                    else:
                        self.db.update_phone_delete_state(file_path, "FAILED")
                self.db.commit()

                _del_total = max(1, int(len(uploaded_files)))
                _del_rate = float(len(deleted_set)) / float(_del_total)
                _all_deleted = (len(deleted_set) >= _del_total)

                # v1.7.9 WARNING 集約: 成功率 が 100% 未満 なら 1 WARNING だけ 出す
                # v1.7.9 WARNING aggregation: emit a single WARNING when success rate < 100%
                if not _all_deleted:
                    try:
                        from pixel_sync.core.logger import Logger as _DelWarn
                        rate_pct = round(_del_rate * 100.0, 1)
                        _DelWarn.warning(
                            tr("log.flow.phase6_ng").format(batch=batch_index, total=len(batches), rate=rate_pct, ok=len(deleted_set), n=_del_total)
                        )
                    except Exception:
                        pass

                try:
                    from pixel_sync.core.logger import Logger as _DelOK
                    _DelOK.success(
                        tr("log.flow.phase6_ok").format(batch=batch_index, total=len(batches), d=len(deleted_set), n=len(uploaded_files))
                    )
                    _events.publish(
                        _events.PIXEL_STORAGE_RECOVERED,
                        {
                            "batch_index": batch_index,
                            "deleted": len(deleted_set),
                            "files": len(uploaded_files),
                            "batch_bytes": int(_batch_total_bytes),
                        },
                    )
                except Exception:
                    pass
            else:
                # 1 ファイル だけ FAIL し た よう な 軽微 な ケース で バッチ 全体 を FAIL 扱い し ない 緩和 ルール
                # Relaxation rule: do not mark the whole batch as FAILED for a minor single-file failure.
                try:
                    threshold = int(FLOW_FAIL_THRESHOLD_BATCH_FAIL_COUNT or 0)
                except Exception:
                    threshold = 0
                if threshold > 0 and (failed <= threshold) and (success >= 0):
                    try:
                        from pixel_sync.core.logger import Logger as _LFL
                        _LFL.warning(
                            tr("log.flow.batch_relaxed").format(batch=batch_index, total=len(batches), failed=failed, threshold=threshold)
                        )
                    except Exception:
                        pass
                    batch_status = "DONE"
                else:
                    batch_status = "FAILED"
                    for file_path, _ in uploaded_files:
                        self.db.update_google_photos_state(file_path, "FAILED")
                    self.db.commit()

            # ============================================
            # batch summary + history insert
            # ============================================
            print()
            print(f"  Batch {batch_index}/{len(batches)}: {batch_status}")
            print(f"    SUCCESS : {success}")
            print(f"    SKIPPED : {skipped}")
            print(f"    FAILED  : {failed}")
            print(f"    Backup OK: {backup_ok} (recovery_count={recovery_count})")
            print(f"    Upload time : {round(upload_seconds, 1)}s")
            print(f"    Backup time : {round(backup_seconds, 1)}s")

            try:
                batch_completed = datetime.now()
                elapsed = time.time() - batch_started_ts
                self.db.insert_batch_history(
                    started_at=batch_started.isoformat(timespec="seconds"),
                    completed_at=batch_completed.isoformat(timespec="seconds"),
                    file_count=len(batch),
                    total_bytes=sum(Uploader._safe_size(p) for p, _ in batch),
                    elapsed_seconds=elapsed,
                    upload_seconds=upload_seconds,
                    backup_seconds=backup_seconds,
                    status=batch_status,
                )
                self.db.commit()
            except Exception:
                pass

        print()
        print("=" * 60)
        print(tr("upload.complete"))
        print("=" * 60)
        print(f"{tr('status.success'):<8}: {success:,}")
        print(f"{tr('status.skip'):<8}: {skipped:,}")
        print(f"{tr('status.failed'):<8}: {failed:,}")

        return (success, skipped, failed)

