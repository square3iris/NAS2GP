# ============================================
# FILE: nas2gp/uploader.py
# ============================================

from datetime import datetime
import time

from nas2gp.core.config import COMMIT_INTERVAL
from nas2gp.storage.db import Database
from nas2gp.worker.backup_waiter import UploadWorker, compute_idle_timeout
from nas2gp.core.i18n import tr
from datetime import timedelta
from pathlib import Path

from nas2gp.core.settings import (
    RESTART_PHOTOS_AFTER_PUSH,
    FAILSAFE_RECOVERY_THRESHOLD_PER_BATCH,
    FAILSAFE_RESTART_ON_BACKUP_TIMEOUT,
    FLOW_FAIL_THRESHOLD_BATCH_FAIL_COUNT,
)
from nas2gp.core import settings as _settings_module
from nas2gp.core import events as _events
from nas2gp.core import control as _control


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
        from nas2gp.core.util import publish_phase as _pp
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
        # v1.13.9: True の とき 、 前回 の 途中 停止 チェックポイント から 再開 する 。
        # v1.13.9: When True, resume from the previous interrupted-run checkpoint.
        self.resume_mode = False

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
            from nas2gp.core import settings as _set
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

        files = self._normalize(files)

        # v1.14.0: 取り残し復旧 候補 (端末 上 に 残っ た まま Google Photos 未確認 の ファイル)。
        #   転送 完了 (T=DONE) かつ GP 未確認 (G=PENDING) かつ 端末 削除 未完了 (D!=DONE) の
        #   行 は 、 再転送 対象 (T=PENDING 限定) にも Pre-1 GC (G=DONE 限定) にも 該当 せず 、
        #   中断 した バッチ の 取り残し として 端末 容量 を 占有 し 続け る 。
        # v1.14.0: leftover-recovery candidates (transferred but GP-unconfirmed files left
        #   on the device). They match neither the re-transfer list (T=PENDING only) nor
        #   Pre-1 GC (G=DONE only), so abandoned batches keep occupying device space.
        try:
            _leftover_cand = self.db.get_unconfirmed_transferred_files() or []
        except Exception:
            _leftover_cand = []

        if not files and not _leftover_cand:
            print(tr("upl.info.nothing"))
            return

        device_files = self.worker.camera_files()

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
        # v1.13.9: 途中 停止 から の 再開 (resume) チェックポイント
        #   バッチ 内 フェーズ の 途中 で 停止 し た 場合 、 次回 開始 時 に 「途中 から
        #   再開 します か ?」 と 確認 し 、 YES なら 保存 済み の バッチ と フェーズ から
        #   再開 する 。 NO なら チェックポイント を 破棄 し て 最初 から 実行 する 。
        # v1.13.9: Resume checkpoint for interrupted runs. When a run stops mid-phase,
        #   the next start can ask "resume from where you left off?" and continue from
        #   the saved batch/phase (YES), or discard the checkpoint and start over (NO).
        # ====================================================================
        _resume_phase = 0
        _resume_uploaded = []
        _resume = None
        try:
            if getattr(self, "resume_mode", False):
                _resume = self.db.get_resume_checkpoint()
        except Exception:
            _resume = None
        if _resume:
            try:
                _rb = [tuple(x) for x in (_resume.get("batch_files") or [])]
            except Exception:
                _rb = []
            if _rb:
                _rb_paths = {str(p) for (p, _n) in _rb}
                _tail = [f for f in files if str(f[0]) not in _rb_paths]
                batches = [_rb] + self._build_batches(_tail, _MAX_FILES_PER_BATCH, _MAX_BYTES_PER_BATCH)
                _resume_phase = int(_resume.get("phase_no") or 0)
                try:
                    _resume_uploaded = [tuple(x) for x in (_resume.get("uploaded_files") or [])]
                except Exception:
                    _resume_uploaded = []
                try:
                    from nas2gp.core.logger import Logger as _RsmLg
                    _RsmLg.info(tr("log.flow.resume_start").format(
                        batch=int(_resume.get("batch_index") or 1),
                        phase=_resume_phase,
                        n=len(_rb),
                    ))
                except Exception:
                    pass
            else:
                _resume = None

        # ====================================================================
        # v1.14.0: 取り残し復旧 バッチ
        #   端末 に 実在 する 取り残し (T=DONE / G=PENDING / D!=DONE) を 先頭 バッチ として
        #   差し込む 。 転送 は 行わ ず 、 メディアスキャン → GP 起動 → バックアップ 完了待ち
        #   → 削除 だけ を やり直す 。 GP 確認 前 に は 削除 し ない ため 、 「未 バックアップ の
        #   まま 端末 から 消える」 事故 は 起き ない 。 途中 再開 チェックポイント が ある 場合は
        #   そちら を 優先 し 、 取り残し は 次回 実行 で 処理 する 。
        # v1.14.0: Leftover-recovery batch. Leftovers still present on the device are
        #   prepended as leading batches; transfer is skipped and only media-scan -> GP
        #   start -> backup wait -> delete are re-run. Nothing is deleted before GP
        #   confirmation, so no un-backed-up file is ever lost. An explicit resume
        #   checkpoint takes priority; leftovers are handled on the next run.
        # ====================================================================
        recover_paths = set()
        if not _resume and _leftover_cand:
            try:
                _dev_names = {str(x).lower() for x in device_files}
                _rec = [
                    (str(p), str(n)) for (p, n) in _leftover_cand
                    if str(n).lower() in _dev_names
                ]
            except Exception:
                _rec = []
            if _rec:
                _rec_bytes = 0
                try:
                    _rec_bytes = sum(Uploader._safe_size(p) for p, _ in _rec)
                except Exception:
                    _rec_bytes = 0
                try:
                    from nas2gp.core.logger import Logger as _RecLg
                    _RecLg.info(tr("log.flow.leftover_start").format(
                        n=len(_rec), gb=round(_rec_bytes / 1024**3, 2)))
                except Exception:
                    pass
                try:
                    print(tr("log.flow.leftover_start").format(
                        n=len(_rec), gb=round(_rec_bytes / 1024**3, 2)))
                except Exception:
                    pass
                _rec_batches = self._build_batches(_rec, _MAX_FILES_PER_BATCH, _MAX_BYTES_PER_BATCH)
                batches = _rec_batches + batches
                recover_paths = {str(p) for (p, _n) in _rec}

        def _save_checkpoint(_bi, _phase_no, _batch_files, _uploaded):
            """バッチ 内 フェーズ の 開始 点 を 保存 する (途中 停止 時 の 再開 用)。
            Persist the phase start point so an interrupted run can resume."""
            try:
                self.db.set_resume_checkpoint({
                    "batch_index": int(_bi),
                    "total_batches": len(batches),
                    "phase_no": int(_phase_no),
                    "batch_files": [[str(p), str(n)] for (p, n) in _batch_files],
                    "uploaded_files": [[str(p), str(n)] for (p, n) in (_uploaded or [])],
                    "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
            except Exception:
                pass

        def _clear_checkpoint():
            try:
                self.db.clear_resume_checkpoint()
            except Exception:
                pass

        # ====================================================================
        # v1.7.9 共通 ヘルパー: 強力 ADB 再接続 (Pre1 / Pre0 / 各 Phase 開始 前 に 必ず 呼び出し)
        # ====================================================================
        # ====================================================================
        # v1.7.9 shared helper: strong ADB reconnect
        # (always called before Pre1 / Pre0 / each phase start)
        # ====================================================================
        def _ensure_adb_robust(label: str = "", publish_unrecoverable: bool = True):
            try:
                import nas2gp.core.settings as _ens_s
                policy = str(getattr(_ens_s, "ADB_FAILURE_POLICY", "pause_dialog") or "pause_dialog").strip().lower()
            except Exception:
                policy = "pause_dialog"
            try:
                ok = bool(self.worker.device.ensure_adb_alive(
                    aggressive=True,
                    shell_probe=True,
                    publish_unrecoverable=bool(publish_unrecoverable and policy == "pause_dialog"),
                ))
            except Exception:
                ok = False
            # 再接続 / 端末 再起動 で 画面 輝度 が 既定 に 戻る 場合 が ある ため 再適用
            # Re-apply dimming: a reconnect / device reboot may have reset brightness.
            if ok:
                try:
                    from nas2gp.device.ui import apply_night_brightness
                    apply_night_brightness()
                except Exception:
                    pass
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
        _bat_state = {"last_check": 0.0, "last_level": None}

        def _safe_int(v, default):
            try:
                return int(v)
            except Exception:
                return default

        def _safe_float(v, default):
            try:
                return float(v)
            except Exception:
                return default

        def _fmt_temp(v):
            """温度表示用 の 整形 (小数1桁)。 / Format a temperature for display (1 decimal)."""
            try:
                return f"{float(v):.1f}"
            except Exception:
                return str(v)

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
                from nas2gp.core.logger import Logger as _StopLg2
                _StopLg2.error(f"{message} (PENDINGに戻した失敗件数={_reverted})")
            except Exception:
                pass
            try:
                _control.request_stop_now()
            except Exception:
                pass

        def _battery_gate(force: bool = False):
            """残量 が 閾値 未満 、 充電 中 で も 減少 中 、 または 本体 温度 が 上限 を 超え た とき 、
            充電 / 冷却 まで 待機 する 。
            戻り値: "OK" (続行) / "STOPPED" (ユーザー 停止) / "TIMEOUT" (待機 上限 超過)。
            Wait until charging / cooling when the battery is below the threshold, the
            level is falling even though the device reports charging, or the body
            temperature exceeds DEVICE_TEMP_LIMIT_C.
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
            crit = _safe_int(getattr(_settings_module, "BATTERY_CRITICAL_PCT", 10), 10)
            try:
                wait_max = float(getattr(_settings_module, "BATTERY_WAIT_MAX_SEC", 3600) or 0)
            except Exception:
                wait_max = 3600.0

            # v1.14.0: 本体 温度 上限 (℃ )。 0 以下 は 無効 。 冷却 判定 は 上限 -3℃ の
            #   ヒステリシス を 使う (上限 付近 で の 再開 / 停止 の ばたつき 防止)。
            # v1.14.0: Body-temperature limit (deg C). <= 0 disables it. Cooldown uses a
            #   -3 C hysteresis to avoid flapping around the limit.
            _temp_limit = _safe_float(getattr(_settings_module, "DEVICE_TEMP_LIMIT_C", 45.0), 45.0)
            if _temp_limit <= 0.0:
                _temp_limit = 0.0
            _temp_resume = max(0.0, _temp_limit - 3.0) if _temp_limit > 0.0 else 0.0

            def _read_health():
                try:
                    from nas2gp.device.adb import ADB as _ADB_h
                    return _ADB_h.get_device_health() or {}
                except Exception:
                    return {}

            def _level_state(_h):
                """(残量, 充電中, 前回残量) を 返す 。 読め た 残量 は 状態 に 記録 する 。
                Return (level, charging, previous level); a readable level is recorded."""
                _lvl_i = _safe_int(_h.get("battery_level"), -1) if _h.get("battery_level") is not None else -1
                _prev = _bat_state.get("last_level")
                if _lvl_i >= 0:
                    _bat_state["last_level"] = _lvl_i
                return _lvl_i, _h.get("battery_charging"), _prev

            def _temp_of(_h):
                _v = _h.get("battery_temp_c")
                return None if _v is None else _safe_float(_v, None)

            def _rising(_lvl_i, _chg, _prev):
                """充電 中 で 、 かつ 前回 より 残量 が 増え て い る か 。
                True when charging and the level is higher than the previous reading."""
                return bool(_chg is True and _prev is not None and _lvl_i > _prev)

            def _level_ok(_lvl_i, _chg, _prev):
                """続行 可 か 。 絶対下限 未満 は charge 状態 に 関係 なく 続行 し ない 。
                Whether the run may continue. Below the absolute floor it never may,
                and below the threshold it must at least be rising while charging."""
                if 0 <= _lvl_i < crit:
                    return False
                if _lvl_i >= thr:
                    return True
                return _rising(_lvl_i, _chg, _prev)

            def _critical_stop(_lvl_i, _chg, _prev):
                """絶対下限 まで 低下 し 、 かつ 回復 し て い ない → 即 安全停止。
                At/below the absolute floor without recovery -> stop immediately."""
                return bool(0 <= _lvl_i < crit and not _rising(_lvl_i, _chg, _prev))

            def _bat_bad(_lvl_i, _chg, _prev):
                """残量 が 理由 で 待機 すべ き か (読め ない とき は 待機 し ない)。
                Whether the battery requires a wait (an unreadable level does not)."""
                return bool(_lvl_i >= 0 and not _level_ok(_lvl_i, _chg, _prev))

            def _temp_bad(_temp_f, waiting=False):
                """本体 温度 が 理由 で 待機 すべ き か 。 waiting 中 は ヒステリシス 判定 。
                Whether the temperature requires a wait; while waiting, use the hysteresis."""
                if _temp_limit <= 0.0 or _temp_f is None:
                    return False
                return bool(_temp_f > (_temp_resume if waiting else _temp_limit))

            _h = _read_health()
            _lvl_i, _chg, _prev = _level_state(_h)
            _temp_f = _temp_of(_h)
            _bat_bad_i = _bat_bad(_lvl_i, _chg, _prev)
            _temp_bad_i = _temp_bad(_temp_f)
            if not _bat_bad_i and not _temp_bad_i:
                return "OK"
            _start_bat = bool(_bat_bad_i)
            _start_temp = bool(_temp_bad_i)

            # 待機 する 理由 を 記録 する (電池 / 温度)。
            # Record why we paused (battery / temperature).
            try:
                from nas2gp.core.logger import Logger as _BatLg
                if _bat_bad_i:
                    if _lvl_i < crit:
                        _BatLg.error(tr("log.flow.battery_critical").format(level=_lvl_i, floor=crit))
                    elif _chg is True:
                        _BatLg.warning(tr("log.flow.battery_draining").format(level=_lvl_i, thr=thr))
                    else:
                        _BatLg.warning(tr("log.flow.battery_low").format(level=_lvl_i, thr=thr))
                if _temp_bad_i:
                    _BatLg.warning(tr("log.flow.temp_high").format(
                        temp=_fmt_temp(_temp_f), limit=_fmt_temp(_temp_limit)))
            except Exception:
                pass

            _waited = 0.0
            _poll = 15.0
            while True:
                if _control.stop_now_requested():
                    return "STOPPED"
                _remain = max(0, int(wait_max - _waited)) if wait_max > 0 else 0
                if _temp_bad_i:
                    _publish_phase(
                        "TEMP_WAIT",
                        tr("log.flow.temp_wait").format(
                            temp=_fmt_temp(_temp_f), limit=_fmt_temp(_temp_limit), remain=_remain),
                    )
                else:
                    _publish_phase(
                        "BATTERY_WAIT",
                        tr("log.flow.battery_wait").format(level=_lvl_i, thr=thr, remain=_remain),
                    )
                time.sleep(_poll)
                _waited += _poll
                _h = _read_health()
                _lvl_i, _chg, _prev = _level_state(_h)
                _temp_f = _temp_of(_h)
                if _critical_stop(_lvl_i, _chg, _prev):
                    try:
                        from nas2gp.core.logger import Logger as _BatCritLg
                        _BatCritLg.error(tr("log.flow.battery_critical").format(level=_lvl_i, floor=crit))
                    except Exception:
                        pass
                    return "TIMEOUT"
                _bat_bad_i = _bat_bad(_lvl_i, _chg, _prev)
                _temp_bad_i = _temp_bad(_temp_f, waiting=True)
                if not _bat_bad_i and not _temp_bad_i:
                    try:
                        from nas2gp.core.logger import Logger as _BatOkLg
                        if _start_temp:
                            _BatOkLg.success(tr("log.flow.temp_ok").format(temp=_fmt_temp(_temp_f)))
                        if _start_bat:
                            _BatOkLg.success(tr("log.flow.battery_ok").format(
                                level=(_lvl_i if _lvl_i >= 0 else "?")))
                    except Exception:
                        pass
                    return "OK"
                if wait_max > 0 and _waited >= wait_max:
                    try:
                        from nas2gp.core.logger import Logger as _BatNgLg
                        if _temp_bad_i:
                            _BatNgLg.error(tr("log.flow.temp_timeout").format(
                                min=round(wait_max / 60), temp=_fmt_temp(_temp_f)))
                        else:
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
                from nas2gp.device.adb import ADB as _ADB_g
                if _ADB_g.has_device(check_offline_too=False):
                    return "OK"
            except Exception:
                pass
            try:
                wait_max = float(getattr(_settings_module, "ADB_RECOVERY_WAIT_SEC", 300) or 300)
            except Exception:
                wait_max = 300.0
            try:
                from nas2gp.core.logger import Logger as _GateLg
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
                    _ok = bool(self.worker.device.ensure_adb_alive(
                        aggressive=True, shell_probe=True, publish_unrecoverable=False))
                except Exception:
                    _ok = False
                if _ok:
                    try:
                        from nas2gp.core.logger import Logger as _GateOkLg
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
                from nas2gp.core.logger import Logger as _GateNgLg
                _GateNgLg.error(_fail_msg)
            except Exception:
                pass
            return "UNRECOVERABLE"

        # ====================================================================
        # (Pre-1) ★ v1.7.10 STORAGE3: 端末 自動 GC Phase 強化版
        #   実際 に 端末 上 rm 成功 し た ファイル だけ を DB mark_phone_delete_done_bulk に 渡す 。
        #   (旧: 全件 DONE 化 → 失敗 分 が 次回 GC 対象 外 で 永久 残留 する バグ)
        # ====================================================================
        # (Pre-1) v1.7.10 STORAGE3: Enhanced device auto-GC phase
        #   Pass to DB mark_phone_delete_done_bulk only files actually rm'd on device.
        #   (Old: marking all DONE left failed files permanently out of next GC scope)
        # ====================================================================
        try:
            _ensure_adb_robust("Pre-1 GC start", publish_unrecoverable=True)
        except Exception:
            pass
        try:
            from nas2gp.core.logger import Logger as _GcLg
            _gc_list = self.db.get_phone_pending_delete_files() or []
            try:
                _gc_before_free = float(self.worker.device.get_storage_root_free_gb() or 0.0)
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
                        self.worker.device.run_media_scanner_and_wait(3.0)
                    except Exception:
                        pass
                    try:
                        _gc_after_free = float(self.worker.device.get_storage_root_free_gb() or 0.0)
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
                        _events.publish(_events.DEVICE_STORAGE_RECOVERED, {
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
                    from nas2gp.core.logger import Logger as _StopLg
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

            # v1.13.9: 途中 再開 の 場合 、 先頭 バッチ は 保存 済み の フェーズ 番号 から 開始 する
            #   (2=GP停止 / 3=転送 / 4=メディア / 5=GP起動 / 6=バックアップ待ち / 7=削除)。
            # v1.14.0: 取り残し復旧 バッチ は 転送 済み の ため 常に フェーズ 4 から 開始 する 。
            # v1.13.9: When resuming, the first batch starts at the saved phase number
            #   (2=GP stop / 3=upload / 4=media / 5=GP start / 6=backup wait / 7=delete).
            # v1.14.0: leftover-recovery batches always start at phase 4 (already transferred).
            _is_recovery = bool(recover_paths) and bool(batch) and all(
                str(p) in recover_paths for (p, _n) in batch
            )
            if _is_recovery:
                _bphase = 4
            elif batch_index == 1 and _resume_phase > 0:
                _bphase = _resume_phase
            else:
                _bphase = 0

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

            # ---- (Pre-0) ★ v1.7.7 新規: 端末 空き容量 チェック
            #       バッチ 合計 サイズ を 計算 し 、 端末 空き容量 - マージン を 下回っ て
            #       い ない か 確認。 最大 3 回 まで 10秒 間隔 で 再取得 し 、 解消 すれ ば 続行
            #       (Pre-1 GC の 反映 待ち や ユーザー が 端末 側 を 手動 削除 する 猶予)。
            # ---- (Pre-0) v1.7.7: device free-space check
            #       Sum batch size and verify it does not fall below device free space minus margin.
            #       Retry up to 3 times at 10s intervals; continue if resolved
            #       (grace period for Pre-1 GC to apply or user to delete manually on device).
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
            # v1.14.0: 転送 済み バッチ (取り残し復旧 / 途中 再開 で フェーズ 4 以降) の 対象 は
            #   既に 端末 上 に ある (これ から 削除 する) ため 、 追加 の 空き 容量 は 不要 。
            #   要求 サイズ を 0 に し て 、 容量 判定 で 誤って 停滞 / 復旧シーケンス に 入る の を
            #   防ぐ 。
            # v1.14.0: for already-transferred batches (leftover recovery / resume at phase 4+)
            #   the targets already exist on the device (and are about to be deleted), so no
            #   extra free space is needed; require 0 to avoid a spurious Pre-0 stall.
            if _is_recovery or _bphase > 3:
                _need_gb = 0.0
            _margin_gb = float(getattr(__import__("nas2gp.core.settings", fromlist=["_"]), "DEVICE_STORAGE_MARGIN_GB", 4.0))
            _STORAGE_RETRY_MAX = 3
            _STORAGE_RETRY_WAIT = 10
            _storage_ok = False
            _last_free_gb = 0.0
            for _storage_retry_i in range(_STORAGE_RETRY_MAX):
                try:
                    _free_gb = float(self.worker.device.get_storage_root_free_gb() or 0.0)
                except Exception:
                    _free_gb = 0.0
                _last_free_gb = _free_gb
                if _need_gb <= 0 or _free_gb <= 0 or (_need_gb + _margin_gb) <= _free_gb:
                    _storage_ok = True
                    break
                if _storage_retry_i < _STORAGE_RETRY_MAX - 1:
                    _shortage = max(0.0, (_need_gb + _margin_gb) - _free_gb)
                    try:
                        from nas2gp.core.logger import Logger as _WtLg
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
                    from nas2gp.core.logger import Logger as _AutoRcvLg
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
                            from nas2gp.core.logger import Logger as _RcLg
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
                                            from nas2gp.core.logger import Logger as _G1Lg
                                            _G1Lg.info(
                                                tr("log.flow.recover_s1_done").format(seq=_seq_num, max=_AUTO_RECOVER_MAX, deleted=len(_rerun_del), total=len(_gc_rerun_list), db=_db_m)
                                            )
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                        except Exception as _e1:
                            try:
                                from nas2gp.core.logger import Logger as _ErrLg
                                _ErrLg.warning(tr("log.flow.recover_s1_err").format(seq=_seq_num, max=_AUTO_RECOVER_MAX, exc_type=type(_e1).__name__, exc=_e1))
                            except Exception:
                                pass
                        try:
                            self.worker.device.run_media_scanner_and_wait(3.0)
                        except Exception:
                            pass
                        try:
                            _f_s1 = float(self.worker.device.get_storage_root_free_gb() or 0.0)
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
                                    from nas2gp.core.logger import Logger as _OkLg
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
                            from nas2gp.core.logger import Logger as _S3Lg
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
                            _reboot_ok = bool(self.worker.device.reboot_device(wait_seconds_after_reboot=45.0))
                        except Exception as _e3:
                            try:
                                from nas2gp.core.logger import Logger as _E3Lg
                                _E3Lg.warning(tr("log.flow.recover_s3_err").format(seq=_seq_num, max=_AUTO_RECOVER_MAX, exc_type=type(_e3).__name__, exc=_e3))
                            except Exception:
                                pass
                        try:
                            _ensure_adb_robust(f"Pre-0 recover-S3-after B{batch_index}", publish_unrecoverable=True)
                        except Exception:
                            pass
                        try:
                            self.worker.device.run_media_scanner_and_wait(4.0)
                        except Exception:
                            pass
                        try:
                            _f_s3 = float(self.worker.device.get_storage_root_free_gb() or 0.0)
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
                                from nas2gp.core.logger import Logger as _S3OkLg
                                _S3OkLg.success(
                                    tr("log.flow.recover_s3_ok").format(batch=batch_index, total=len(batches), seq=_seq_num, max=_AUTO_RECOVER_MAX, free=_free_gb)
                                )
                            except Exception:
                                pass
                            break
                        try:
                            from nas2gp.core.logger import Logger as _RetryLg
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
                            from nas2gp.core.logger import Logger as _LoopErrLg
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
                        from nas2gp.core.logger import Logger as _FailLg
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
                        f"DEVICE_STORAGE_INSUFFICIENT_FATAL: batch {batch_index}/{len(batches)} "
                        f"need={_need_gb:.2f}GB + margin={_margin_gb:.1f}GB > final_free={_free_gb:.2f}GB "
                        f"(auto-recovery {_AUTO_RECOVER_MAX} loops exhausted)"
                    )
                try:
                    _events.publish(_events.DEVICE_STORAGE_RECOVERED, {
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
                    from nas2gp.core.logger import Logger as _AfterLg
                    _AfterLg.info(
                        tr("log.flow.pre0_ok").format(batch=batch_index, total=len(batches))
                    )
                except Exception:
                    pass
                pass
            if False:
                try:
                    from nas2gp.core.logger import Logger as _StrLg
                    _msg = (
                        tr("log.flow.pre0_insufficient").format(batch=batch_index, total=len(batches), need=_need_gb, margin=_margin_gb, free=_free_gb)
                    )
                    _StrLg.warning(_msg)
                except Exception:
                    pass
                try:
                    _events.publish(
                        _events.DEVICE_STORAGE_INSUFFICIENT,
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
                    from nas2gp.core.logger import Logger as _SkipLg
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
            #   Phase 2/6 NAS → 端末 ファイル 転送
            #   Phase 3/6 Media Scanner broadcast (端末 OS に 新ファイル 認識 させる)
            #   Phase 4/6 Google Photos START + scroll top + CYCLE_GP_DELAY 待機 (全バッチ 必須)
            #   Phase 5/6 Google Photos backup 完了 待機 → 完了確認
            #   Phase 6/6 端末 側 の アップロード完了 ファイル 削除 (backup_ok 時 のみ)
            # ========================================================================
            # ========================================================================
            # v1.7.7: strict per-batch phase order:
            #   1/6 GP STOP, 2/6 NAS->device transfer, 3/6 media scanner broadcast,
            #   4/6 GP START + scroll top + delay, 5/6 wait for backup, 6/6 delete uploaded files.
            # ========================================================================

            # ---- Phase 1/6: Google Photos force-stop
            try:
                _ensure_adb_robust(f"Phase1 BATCH {batch_index}", publish_unrecoverable=True)
            except Exception:
                pass
            try:
                from nas2gp.core import settings as _up_s
                delay_after = int(getattr(_up_s, "CYCLE_GP_DELAY_AFTER_START_SECONDS", 5) or 5)
                from nas2gp.core.logger import Logger as _FLLg
                _FLLg.info(
                    tr("log.flow.phase1_gpstop_log").format(batch=batch_index, total=len(batches), n=len(batch))
                )
            except Exception:
                pass
            print(tr("log.flow.phase1_gpstop"))
            print(tr("log.flow.phase1_gpstop_step"))
            _publish_phase("GP_STOP", tr("log.flow.phase_gpstop").format(batch=batch_index), phase_no=2, total_phases=7, batch_idx=batch_index)
            # v1.13.9: 途中 再開 で Phase 3 以降 から 始める 場合 、 GP は 既に 停止 済み な ので スキップ。
            # v1.13.9: When resuming at phase 3 or later, Google Photos is already stopped; skip.
            if _bphase <= 2:
                try:
                    self.worker.device.stop_google_photos()
                except Exception as e:
                    import traceback as _tb
                    from nas2gp.core.logger import Logger as _LGPstop
                    _LGPstop.warning(f"Phase1 Google Photos stop failed: {type(e).__name__}: {e}\n{_tb.format_exc()}")

            # ---- Phase 2/6: NAS → 端末 転送
            # ---- Phase 2/6: NAS -> device transfer
            try:
                _ensure_adb_robust(f"Phase2 BATCH {batch_index}", publish_unrecoverable=True)
            except Exception:
                pass
            try:
                from nas2gp.core.logger import Logger as _FLLg2
                _FLLg2.info(
                    tr("log.flow.phase2_start_log").format(batch=batch_index, total=len(batches), n=len(batch), gb=round(_batch_total_bytes/1024**3,3))
                )
            except Exception:
                pass
            _publish_phase("UPLOAD", tr("log.flow.phase_upload").format(batch=batch_index, n=len(batch)), phase_no=3, total_phases=7, batch_idx=batch_index)
            print(tr("log.flow.phase2_start").format(n=len(batch), gb=round(_need_gb,3)))
            # v1.13.9: 転送 フェーズ の 開始 点 を 保存 (落ち た とき に ここ から 再開)。
            # v1.13.9: Save the upload phase start point (resume point if the run dies here).
            if _bphase <= 3:
                _save_checkpoint(batch_index, 3, batch, [])

            # v1.8.0: 端末 側 の 作業フォルダ が 無けれ ば 作成 する (既定 /sdcard/DCIM/NAS2GP)。
            # v1.8.0: Ensure the device-side working folder exists (default /sdcard/DCIM/NAS2GP).
            try:
                _work_dir = str(self.worker.device.WORK_DIR or "")
                if _work_dir and not self.worker.device.create_dir(_work_dir):
                    try:
                        from nas2gp.core.logger import Logger as _WdLg
                        _WdLg.warning(tr("log.flow.workdir_mkdir_fail").format(path=_work_dir))
                    except Exception:
                        pass
            except Exception:
                pass

            # v1.14.0: 取り残し復旧 バッチ は その バッチ の 全 ファイル を 転送済み と みなし 、
            #   バックアップ 待ち / 削除 の 対象 に する (再転送 は し ない)。
            # v1.14.0: for a recovery batch every file counts as already uploaded, so it is
            #   included in the backup-wait / delete phases (no re-transfer).
            if _is_recovery:
                uploaded_files = list(batch)
            elif _bphase > 3:
                uploaded_files = list(_resume_uploaded)
            else:
                uploaded_files = []
            recovery_count = 0
            backup_ok = False
            if _bphase > 3:
                # v1.13.9: 転送 済み の 一覧 を 復元 する (再 転送 は せ ず 、 以降 の GP 待ち /
                #   削除 フェーズ だけ を 続行 する)。
                # v1.13.9: Restore the already-uploaded list (skip re-transfer; run only the
                #   subsequent backup-wait / delete phases).
                try:
                    for _p, _n in uploaded_files:
                        device_files.add(_n)
                except Exception:
                    pass

            for (file_path, filename) in ([] if _bphase > 3 else batch):

                # v1.7.15: 即時 停止 要求 → この バッチ の 残り ファイル 転送 を 中断 する。
                #   送信 済み ファイル の GP バックアップ 待ち / 削除 は 安全 の ため 継続 する。
                # v1.7.15: Immediate stop request -> abort remaining file transfers in this batch.
                #   GP backup wait / deletion for already-sent files continues for safety.
                if _control.stop_now_requested():
                    print()
                    print(tr("log.flow.stop_now").format(n=len(batch)))
                    try:
                        from nas2gp.core.logger import Logger as _StopNowLg
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
                        device_files,
                    )
                )

                file_path = resolved_path or file_path
                filename = resolved_name or filename

                if result == "SUCCESS":

                    self.db.update_phone_transfer_state(
                        file_path,
                        "DONE",
                    )

                    device_files.add(filename)

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
                    from nas2gp.device.adb import ADB as _ADB_s
                    _dev_ok = bool(_ADB_s.has_device(check_offline_too=False))
                except Exception:
                    _dev_ok = False
                if not _dev_ok:
                    try:
                        from nas2gp.core.logger import Logger as _SkipLg
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
                from nas2gp.core.logger import Logger as _MSLg
                if len(uploaded_files) > 0:
                    _MSLg.info(tr("log.flow.phase3_log").format(batch=batch_index, total=len(batches), n=len(uploaded_files)))
            except Exception:
                pass
            print()
            print("Media Scanner (Batch)")
            _publish_phase("MEDIA_SCAN", tr("log.flow.phase_mediascan").format(batch=batch_index), phase_no=4, total_phases=7, batch_idx=batch_index)
            # v1.13.9: メディア スキャン フェーズ の 開始 点 を 保存。
            # v1.13.9: Save the media-scan phase start point.
            if _bphase <= 4:
                _save_checkpoint(batch_index, 4, batch, uploaded_files)
            names_only = [n for _, n in uploaded_files]
            if _bphase <= 4:
                self.worker.device.scan_media_batch(names_only)

            # ---- Phase 4/6: Google Photos START + scroll top + delay 待機 (全バッチ 強制実行)
            # ---- Phase 4/6: Google Photos START + scroll top + delay wait (forced for every batch)
            if len(uploaded_files) > 0 and _bphase <= 5:
                # v1.13.9: GP 起動 フェーズ の 開始 点 を 保存。
                # v1.13.9: Save the Google Photos start phase point.
                _save_checkpoint(batch_index, 5, batch, uploaded_files)
                _publish_phase("GP_START", tr("log.flow.phase_gpstart").format(batch=batch_index), phase_no=5, total_phases=7, batch_idx=batch_index)
                try:
                    from nas2gp.core import settings as _up_s2
                    delay_after = int(getattr(_up_s2, "CYCLE_GP_DELAY_AFTER_START_SECONDS", 5) or 5)
                    try:
                        from nas2gp.core.logger import Logger as _FSLg
                        _FSLg.info(
                            tr("log.flow.phase4_log").format(batch=batch_index, total=len(batches), delay=delay_after)
                        )
                    except Exception:
                        pass
                    print(
                        tr("log.flow.phase4").format(delay=delay_after)
                    )
                    self.worker.device.start_google_photos()
                    time.sleep(max(0, int(delay_after)))
                    from nas2gp.core.logger import Logger as _U3
                    _U3.success(
                        tr("log.flow.gp_cycle_ok_log").format(batch=batch_index, total=len(batches))
                    )
                    print(
                        tr("log.flow.gp_cycle_ok").format(delay=delay_after)
                    )
                except Exception as e:
                    import traceback as _tb2
                    from nas2gp.core.logger import Logger as _L4
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
                from nas2gp.core.logger import Logger as _WBLg
                _WBLg.info(
                    tr("log.flow.phase5_log").format(
                        batch=batch_index, total=len(batches),
                        timeout=compute_idle_timeout(None, locals().get("_batch_total_bytes", 0)),
                    )
                )
            except Exception:
                pass
            _publish_phase("WAIT_BACKUP", tr("log.flow.phase_waitbackup").format(batch=batch_index), phase_no=6, total_phases=7, batch_idx=batch_index)
            print()
            print(tr("backup.wait"))
            # v1.13.9: バックアップ 待機 フェーズ の 開始 点 を 保存。
            # v1.13.9: Save the backup-wait phase start point.
            if _bphase <= 6:
                _save_checkpoint(batch_index, 6, batch, uploaded_files)

            batch_status = "DONE"

            backup_started_ts = time.time()
            if _bphase > 6:
                # v1.13.9: Phase 6 (削除) から の 再開 。 削除 は backup_ok 時 のみ 実行 さ れる ため 、
                #   ここ に 到達 し た 時点 で バックアップ は 完了 済み と みなす 。
                # v1.13.9: Resuming from phase 6 (delete). Since deletion runs only when
                #   backup_ok, reaching here means the backup already completed.
                backup_ok = True
                recovery_count = 0
            else:
                backup_result = self.worker.wait_backup_complete(batch_bytes=_batch_total_bytes)
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
                from nas2gp.core.logger import Logger as _BkEnd
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
                self.worker.device.restart_google_photos()
            else:
                print(
                    tr("upl.skip.header").format(
                        bok=int(backup_ok),
                        rc=recovery_count,
                        threshold=FAILSAFE_RECOVERY_THRESHOLD_PER_BATCH,
                    )
                )
                print(tr("upl.skip.reason_no_kill"))

            # ---- Phase 6/6: 端末 側 アップロード 完了 ファイル 削除 (backup_ok 時 のみ)
            # ---- Phase 6/6: Delete uploaded-complete files on device (only when backup_ok)
            if backup_ok:

                try:
                    _ensure_adb_robust(f"Phase6 BATCH {batch_index}", publish_unrecoverable=True)
                except Exception:
                    pass

                try:
                    from nas2gp.core.logger import Logger as _DelLg
                    _DelLg.info(
                        tr("log.flow.phase6_log").format(batch=batch_index, total=len(batches), n=len(uploaded_files))
                    )
                except Exception:
                    pass
                _publish_phase("DELETE", tr("log.flow.phase_delete").format(batch=batch_index, n=len(uploaded_files)), phase_no=7, total_phases=7, batch_idx=batch_index)
                print()
                print(tr("log.flow.phase6"))
                # v1.13.9: 端末 削除 フェーズ の 開始 点 を 保存。
                # v1.13.9: Save the device-delete phase start point.
                _save_checkpoint(batch_index, 7, batch, uploaded_files)

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
                        from nas2gp.core.logger import Logger as _DelWarn
                        rate_pct = round(_del_rate * 100.0, 1)
                        _DelWarn.warning(
                            tr("log.flow.phase6_ng").format(batch=batch_index, total=len(batches), rate=rate_pct, ok=len(deleted_set), n=_del_total)
                        )
                    except Exception:
                        pass

                try:
                    from nas2gp.core.logger import Logger as _DelOK
                    _DelOK.success(
                        tr("log.flow.phase6_ok").format(batch=batch_index, total=len(batches), d=len(deleted_set), n=len(uploaded_files))
                    )
                    _events.publish(
                        _events.DEVICE_STORAGE_RECOVERED,
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
                        from nas2gp.core.logger import Logger as _LFL
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

            # v1.13.9: この バッチ を 完了 し た ので 再開 チェックポイント を 破棄 する
            #   (次 の バッチ の 開始 点 で 改めて 保存 さ れる)。
            # v1.13.9: This batch finished, so drop the resume checkpoint
            #   (it will be re-saved at the next batch's phase start).
            _clear_checkpoint()

        # v1.13.9: 全 バッチ 完了 → 再開 チェックポイント を 破棄。
        # v1.13.9: All batches done -> drop the resume checkpoint.
        _clear_checkpoint()

        print()
        print("=" * 60)
        print(tr("upload.complete"))
        print("=" * 60)
        print(f"{tr('status.success'):<8}: {success:,}")
        print(f"{tr('status.skip'):<8}: {skipped:,}")
        print(f"{tr('status.failed'):<8}: {failed:,}")

        return (success, skipped, failed)

