# ============================================
# FILE: nas2gp/worker.py
# PURPOSE: アップロード実行と Google フォトのバックアップ完了待機を行うワーカー。
#          Worker for uploading files and waiting for Google Photos backup completion.
# ============================================

import time
from collections import deque
from pathlib import Path

from nas2gp.device.device import Device
from nas2gp.core.config import MAX_RETRY, RETRY_WAIT
from nas2gp.core.settings import (
    BACKUP_TIMEOUT,
    BACKUP_ABSOLUTE_MAX_SECONDS,
    MIN_BACKUP_THROUGHPUT_MBPS,
    BACKUP_STALLED_LIMIT,
    BACKUP_CHECK_INTERVAL,
    MIN_ACCEPT_ELAPSED_SECONDS,
    MIN_COMPLETE_STREAK,
    RECENT_WINDOW,
    COMPLETE_QUORUM_RATIO,
    FORCE_ULTRA_ELAPSED_SECONDS,
    FORCE_ULTRA_MIN_COMPLETE_HITS,
    ULTRA_SAFE_ELAPSED_SECONDS,
    FIRST_COMPLETE_ACCEPT_DELAY_SECONDS,
    STACK_TARGET_STATUSES,
    PUSH_INNER_RETRY,
    PUSH_INNER_RETRY_WAITS,
)

from nas2gp.device.ui import (
    get_backup_status,
    prepare_ui,
    try_close_tutorial,
    get_last_detailed_status,
    extract_google_photos_progress,
    reapply_night_brightness,
    keep_screen_off,
)
from nas2gp.core.logger import Logger
from nas2gp.core.i18n import tr
from nas2gp.core import events as _events


def compute_idle_timeout(base_timeout=None, batch_bytes=None) -> int:
    """v1.11.1: 無進捗 タイムアウト の 実効 値 (秒) を 計算 する 。
    基準 の BACKUP_TIMEOUT を 、 バッチ 合計 サイズ ÷ 想定 速度 (MIN_BACKUP_THROUGHPUT_MBPS)
    で 延長 し 、 絶対 上限 (BACKUP_ABSOLUTE_MAX_SECONDS) で キャップ する 。
    単一 の 大容量 ファイル で は 残り 枚数 が 減ら ず ETA も 得 られ ない ため 、
    固定 値 で は 誤って 打ち切る こと を 防ぐ 。
    v1.11.1: Compute the effective no-progress timeout (seconds). Extend the base
    BACKUP_TIMEOUT by batch_bytes / assumed speed, capped by the absolute maximum."""
    from nas2gp.core import settings as _settings
    if base_timeout is None:
        base_timeout = int(getattr(_settings, "BACKUP_TIMEOUT", BACKUP_TIMEOUT))
    _idle = int(base_timeout)
    _abs_max = int(
        getattr(_settings, "BACKUP_ABSOLUTE_MAX_SECONDS", BACKUP_ABSOLUTE_MAX_SECONDS)
    )
    try:
        _bb = int(batch_bytes or 0)
        _mbps = float(getattr(
            _settings, "MIN_BACKUP_THROUGHPUT_MBPS", MIN_BACKUP_THROUGHPUT_MBPS
        ) or 0.0)
        if _bb > 0 and _mbps > 0:
            _est = int(_bb / (_mbps * 1024 * 1024))
            if _est > _idle:
                _idle = _est
    except Exception:
        pass
    if _idle > _abs_max:
        _idle = _abs_max
    if _idle < 1:
        _idle = 1
    return _idle


def compute_stall_limit(idle_timeout=None, interval=None) -> int:
    """v1.11.1: 停滞 リスタート の 実効 しきい値 (連続 回数) を 計算 する 。
    無進捗 タイムアウト が 延長 さ れ た 場合 (大容量 バッチ) のみ 、 しきい値 を
    その 時間 に 比例 させ て 緩和 し 、 進行 中 の 大容量 ファイル で Google Photos を
    誤って 再起動 し ない よう に する 。 小さい バッチ で は 従来 通り (基準 回数 の まま)。
    0 以下 を 返し た 場合 は 停滞 リスタート を 行わ ない (無効)。
    v1.11.1: Compute the effective stall-restart threshold (consecutive polls).
    Only when the no-progress timeout was extended (large batch) is the threshold
    relaxed in proportion, so an in-flight large file is not force-restarted.
    Small batches keep the base threshold. A return value <= 0 disables it."""
    from nas2gp.core import settings as _settings
    base = int(getattr(_settings, "BACKUP_STALLED_LIMIT", BACKUP_STALLED_LIMIT))
    if base <= 0:
        return 0
    if interval is None:
        interval = int(getattr(_settings, "BACKUP_CHECK_INTERVAL", BACKUP_CHECK_INTERVAL))
    interval = max(1, int(interval))
    base_timeout = int(getattr(_settings, "BACKUP_TIMEOUT", BACKUP_TIMEOUT))
    if idle_timeout is None:
        idle_timeout = base_timeout
    if int(idle_timeout) <= base_timeout:
        return base
    return max(base, int(int(idle_timeout) / interval))


class UploadWorker:

    def __init__(self):

        self.device = Device()
        # v1.13.9: バックアップ 待機 中 の 電池 監視 状態 (前回 の 確認 時刻)。
        #   Battery-watch state for the backup wait (last check time).
        self._bat_state = {"last_check": 0.0}

    def _battery_gate(self):
        """v1.14.2: Google Photos の バックアップ 待機 中 の 電池 保護 ゲート 。
        残量 が 閾値 未満 の とき は 即 停止 せず 、 回復 する まで 待機 する
        (転送 フェーズ の Uploader._battery_gate() と 同じ 挙動 に 統一)。
        充電 状態 は 判定 に 使わ ない (表示 専用)。
        戻り値: (status, waited_seconds)
          status = "OK"      : 続行 可
                   "STOPPED" : 停止 要求 (ユーザー / 保護 停止 要求)
                   "TIMEOUT" : 保護 停止 (絶対 下限 割れ 、 または 待機 上限 超過)
        v1.14.2: battery protection gate during the Google Photos backup wait.
        Instead of stopping immediately below the threshold, wait until the level
        recovers (same behaviour as Uploader._battery_gate()). The charging flag is
        display-only. Returns (status, waited_seconds); status is "OK" / "STOPPED" /
        "TIMEOUT" (below the absolute floor, or the wait limit was exceeded).
        """
        try:
            from nas2gp.core import settings as _s
        except Exception:
            return "OK", 0.0
        try:
            interval = float(getattr(_s, "BATTERY_CHECK_INTERVAL_SEC", 600) or 0)
        except Exception:
            interval = 600.0
        _now = time.time()
        if interval > 0 and (_now - self._bat_state["last_check"]) < interval:
            return "OK", 0.0
        self._bat_state["last_check"] = _now

        def _read_level():
            """読める 残量 を 返す (読め ない とき は -1)。
            Return the readable level (-1 when unreadable)."""
            try:
                from nas2gp.device.adb import ADB as _ADB
                _h = _ADB.get_device_health() or {}
            except Exception:
                return -1
            _lvl = _h.get("battery_level")
            if _lvl is None:
                return -1
            try:
                return int(_lvl)
            except Exception:
                return -1

        def _publish(_phase, _detail=""):
            try:
                from nas2gp.core.util import publish_phase as _pp
                _pp(phase=_phase, detail=_detail)
            except Exception:
                pass

        def _stop_now():
            try:
                from nas2gp.core import control as _control
                # v1.14.2: 保護 停止 で ある こと を 記録 し 、 ユーザー 操作 と し て
                #   表示 さ れ ない よう に する 。
                # v1.14.2: record that this is a protection stop so it is not
                #   reported as a user action.
                _control.request_stop_now(reason=_control.REASON_PROTECTION)
            except Exception:
                pass

        def _stop_requested():
            try:
                from nas2gp.core import control as _control
                return bool(_control.stop_now_requested())
            except Exception:
                return False

        try:
            thr = int(getattr(_s, "BATTERY_LOW_THRESHOLD_PCT", 20) or 20)
        except Exception:
            thr = 20
        try:
            crit = int(getattr(_s, "BATTERY_CRITICAL_PCT", 10) or 10)
        except Exception:
            crit = 10
        try:
            wait_max = float(getattr(_s, "BATTERY_WAIT_MAX_SEC", 3600) or 0)
        except Exception:
            wait_max = 3600.0

        _lvl_i = _read_level()
        if _lvl_i < 0 or _lvl_i >= thr:
            return "OK", 0.0

        if _lvl_i < crit:
            # 絶対 下限 割れ → 充電 状態 に 関係 なく 直ちに 安全 停止 。
            _msg = tr("log.flow.battery_critical").format(level=_lvl_i, floor=crit)
            try:
                Logger.error(_msg)
            except Exception:
                pass
            _publish("STOPPED", _msg)
            _stop_now()
            return "TIMEOUT", 0.0

        # 閾値 未満 (危険域 未満 で は ない) → 回復 する まで 待機 する 。
        try:
            Logger.warning(tr("log.flow.battery_low").format(level=_lvl_i, thr=thr))
        except Exception:
            pass
        _waited = 0.0
        # v1.14.2: 待機 中 の 残量 読み直し 間隔 は 設定 値 (分) を 使う (既定 10分)。
        #   停止 要求 に は 待機 中 でも 即 応 する ため 、 sleep を 1秒 刻み に 分割 する 。
        # v1.14.2: battery re-check interval comes from the setting (minutes,
        #   default 10). The sleep is split into 1 s ticks so a stop request is
        #   honoured immediately during the wait.
        try:
            _poll = float(getattr(_s, "BATTERY_WAIT_POLL_MIN", 10) or 10) * 60.0
        except Exception:
            _poll = 600.0
        if _poll <= 0:
            _poll = 600.0
        _tick = 1.0
        while True:
            if _stop_requested():
                return "STOPPED", _waited
            _remain = max(0, int(wait_max - _waited)) if wait_max > 0 else 0
            _publish("BATTERY_WAIT", tr("log.flow.battery_wait").format(
                level=_lvl_i, thr=thr, remain=_remain))
            _slept = 0.0
            while _slept < _poll:
                if _stop_requested():
                    return "STOPPED", _waited
                # v1.14.2: 回復待ち の 間 は UI を 使わ ない ため 画面 を OFF に 保つ
                #   (点灯 した まま だ と 充電 が 進ま ない)。 再送 は 内部 で 間引か れ る 。
                # v1.14.2: Keep the screen off while waiting for recovery (a lit
                #   screen prevents charging). The re-send is throttled internally.
                try:
                    keep_screen_off()
                except Exception:
                    pass
                _step = min(_tick, _poll - _slept)
                time.sleep(_step)
                _slept += _step
            _waited += _poll
            _lvl_i = _read_level()
            if 0 <= _lvl_i < crit:
                _msg = tr("log.flow.battery_critical").format(level=_lvl_i, floor=crit)
                try:
                    Logger.error(_msg)
                except Exception:
                    pass
                _publish("STOPPED", _msg)
                _stop_now()
                return "TIMEOUT", _waited
            if _lvl_i < 0 or _lvl_i >= thr:
                try:
                    Logger.success(tr("log.flow.battery_ok").format(
                        level=(_lvl_i if _lvl_i >= 0 else "?")))
                except Exception:
                    pass
                return "OK", _waited
            if wait_max > 0 and _waited >= wait_max:
                _msg = tr("log.flow.battery_timeout").format(min=round(wait_max / 60))
                try:
                    Logger.error(_msg)
                except Exception:
                    pass
                _publish("STOPPED", _msg)
                _stop_now()
                return "TIMEOUT", _waited

    def camera_files(self):

        return self.device.camera_files()

    def upload(self, file_path, device_files):

        filename = Path(file_path).name
        local_path = Path(file_path)

        for p in device_files:
            if isinstance(p, str) and p.lower() == filename.lower():
                Logger.success(f"SKIP (already on the device) : {filename}")
                return ("SKIP", str(local_path), filename)

        if not local_path.exists():
            Logger.error(tr("sync.err.db_local_missing").format(local_path=local_path))
            return ("FAILED", str(local_path), filename)

        if not local_path.is_file():
            Logger.error(f"LOCAL NOT A FILE : {local_path}")
            return ("FAILED", str(local_path), filename)

        try:
            file_size = local_path.stat().st_size
            if file_size <= 0:
                Logger.error(f"LOCAL FILE EMPTY (0 bytes): {local_path}")
                return ("FAILED", str(local_path), filename)
        except OSError as e:
            Logger.error(f"LOCAL STAT FAILED [{e}] : {local_path}")
            return ("FAILED", str(local_path), filename)

        try:
            inner_max = int(PUSH_INNER_RETRY)
        except Exception:
            inner_max = 5
        if inner_max < 1:
            inner_max = 3

        _last_push_was_offline = False

        for attempt in range(inner_max):

            ok = self.device.upload(
                str(local_path),
                filename,
            )

            if ok:
                device_files.add(filename)
                Logger.success(
                    f"OK  ({file_size / 1024:,.1f} KB) : {filename}"
                )
                return ("SUCCESS", str(local_path), filename)

            try:
                waits = list(PUSH_INNER_RETRY_WAITS or [])
            except Exception:
                waits = []
            if len(waits) >= inner_max:
                try:
                    wait_s = float(waits[attempt])
                except Exception:
                    wait_s = 1.0
            elif waits:
                try:
                    wait_s = float(waits[min(attempt, len(waits) - 1)])
                except Exception:
                    wait_s = 1.0
            else:
                wait_s = float(RETRY_WAIT) if RETRY_WAIT is not None else 1.0
            # v1.7.6: デバイス 完全 不通 (has_device=False) の とき は 長い backoff を 掛け て も
            #   復旧 する 見込み が 薄い ので 待機 時間 を 最大 2 秒 に 制限 し 、
            #   次 の 外側 ループ ( または ユーザー に よる 再接続 操作 ) に 速やか に 譲る 。
            # v1.7.6: When the device is completely offline, cap the wait at ~2s
            #   so the outer loop (or a user reconnect) can recover sooner.
            try:
                from nas2gp.device.adb import ADB as _ADB_ref
                _offline_now = bool(not _ADB_ref.has_device(check_offline_too=False))
            except Exception:
                _offline_now = False
            if _offline_now:
                _last_push_was_offline = True
                wait_s = min(float(wait_s), 1.5)
            else:
                _last_push_was_offline = False
            if wait_s < 0.1:
                wait_s = 0.1
            # v1.7.6: RETRY 文言 の 先頭 に offline/online 状態 を 付記 し 、 また 全く 同じ ファイル で
            #   連続 offline の とき は 2 回 目 以降 の WARNING を info レベル に 落とす こと で
            #   1 ファイル あたり 5 行 も の 重複 WARNING が 並ぶ の を 抑制 。
            # v1.7.6: Prefix retry messages with the offline/online state and demote
            #   repeated offline warnings to info to avoid duplicate WARNING lines.
            _retry_msg = tr("log.gp.retry").format(n=attempt + 1, max=inner_max, wait=wait_s, filename=filename)
            if _offline_now and attempt >= 1:
                Logger.info(
                    f"[DEVICE_OFFLINE] {_retry_msg}"
                )
            else:
                Logger.warning(
                    (f"[DEVICE_OFFLINE] " if _offline_now else "") + _retry_msg
                )
            time.sleep(wait_s)

        Logger.error(f"FAILED: {filename}")
        return ("FAILED", str(local_path), filename)

    def wait_backup_complete(
        self,
        interval=None,
        timeout=None,
        batch_bytes=None,
    ):
        """
        Googleフォトのバックアップ完了を待機する (v1.5.3 4段階受理ロジック)

        受理条件（優先順位、いずれかを満たしたら完了とみなす）:
          A. 厳格(strict):     backing_up を経由したあとで complete を検出
          B. 緩和(relaxed):    waiting/backing_up を一度でも経由し、
                                MIN_COMPLETE_STREAK 回連続 complete、
                                かつ MIN_ACCEPT_ELAPSED_SECONDS 以上経過
          D. Force Ultra:       FORCE_ULTRA_ELAPSED_SECONDS 以上経過 かつ
                                最近 RECENT_WINDOW 中に
                                FORCE_ULTRA_MIN_COMPLETE_HITS 回以上 complete
                                を観測していれば強制受理
          C. 最終保険(timeout): timeout到達時、最近 RECENT_WINDOW 中の
                                complete 割合 >= COMPLETE_QUORUM_RATIO

        Waits for Google Photos backup completion using the v1.5.3 4-stage
        acceptance rules (strict / relaxed / force-ultra / timeout-quorum).
        """

        from nas2gp.util import print_phase

        # v1.7.15: config_ui.json の 変更 を 即 反映 する ため 、 import 時 定数 で は なく
        #   settings モジュール から 実行時 に 読み直す 。
        # v1.7.15: Re-read from the settings module at runtime (not import-time constants)
        #   so config_ui.json changes take effect immediately.
        from nas2gp.core import settings as _settings

        if interval is None:
            interval = int(getattr(_settings, "BACKUP_CHECK_INTERVAL", BACKUP_CHECK_INTERVAL))
        if timeout is None:
            timeout = int(getattr(_settings, "BACKUP_TIMEOUT", BACKUP_TIMEOUT))
        _abs_max = int(
            getattr(_settings, "BACKUP_ABSOLUTE_MAX_SECONDS", BACKUP_ABSOLUTE_MAX_SECONDS)
        )

        # v1.11.0: 無進捗 タイムアウト を バッチ 合計 サイズ に 比例 さ せ て 延長 する 。
        #   単一 の 大容量 ファイル で は Google Photos の 「残り 枚数」 が 減ら ず 、
        #   ETA も 取得 でき ない こと が ある ため 、 固定 の BACKUP_TIMEOUT で は
        #   誤って 打ち切る (例: 4GB の 動画 が 600 秒 で NG に なる)。
        #   同じ 比例 で 停滞 リスタート の しきい値 も 緩和 し 、 進行 中 の 大容量
        #   ファイル で Google Photos を 誤って 再起動 し ない よう に する 。
        # v1.11.0: Scale the no-progress timeout with the batch size (see
        #   compute_idle_timeout). The stall-restart threshold is relaxed with the
        #   same ratio so an in-flight large file is not force-restarted.
        _idle_timeout = compute_idle_timeout(timeout, batch_bytes)
        _stall_limit = compute_stall_limit(_idle_timeout, interval)
        _stall_base = int(getattr(_settings, "BACKUP_STALLED_LIMIT", BACKUP_STALLED_LIMIT))
        if _stall_limit > 0 and _stall_limit != _stall_base:
            try:
                Logger.info(tr("bw.log.stall_limit_scaled").format(
                    base=_stall_base, scaled=_stall_limit,
                ))
            except Exception:
                pass
        if _idle_timeout != int(timeout):
            # 延長 が 効い た 場合 のみ 1 行 残す (長時間 評価 の 事後 解析 用)。
            # Log one line only when the scaling kicked in (for post-run analysis).
            try:
                _bb = int(batch_bytes or 0)
                _mbps = float(getattr(
                    _settings, "MIN_BACKUP_THROUGHPUT_MBPS", MIN_BACKUP_THROUGHPUT_MBPS
                ) or 0.0)
                Logger.info(tr("bw.log.idle_timeout_scaled").format(
                    base=int(timeout), scaled=_idle_timeout,
                    gb=round(_bb / 1024 ** 3, 2), mbps=_mbps,
                ))
            except Exception:
                pass

        print_phase(tr("backup.waiting"))

        start = time.time()

        unknown_count = 0
        backing_up_count = 0
        tutorial_count = 0

        saw_backing_up = False
        saw_waiting = False
        saw_preparing = False
        preparing_logged = False
        saw_complete_ever = False
        saw_complete_count_total = 0
        first_complete_ts = None   # NEW: 最初に complete を 観測した時刻
        # NEW: Timestamp when "complete" was first observed.
        complete_elapsed = 0.0     # NEW: 最初の complete からの経過秒数 (Gルール用)
        # NEW: Seconds elapsed since the first "complete" (used by rule G).

        complete_streak = 0
        recent_statuses = deque(maxlen=RECENT_WINDOW)

        recovery_count = 0

        # v1.7.15: 進捗 連動 タイムアウト 用 状態 。
        #   timeout は 「開始 から の 経過」 で は な く 「無進捗 の 継続 時間」 で 判定 する 。
        # v1.7.15: State for the progress-linked timeout. The timeout is judged by
        #   continuous no-progress time, not by elapsed time since start.
        last_progress_ts = start
        last_remaining = None
        last_eta = None
        prev_complete_total = 0

        def saw_any_valid_progress():
            return saw_backing_up or saw_waiting or saw_preparing

        lock_count = 0

        while True:

            # v1.13.4: 待機 中 は 画面 Wake / Photos 再起動 で 輝度 が 既定 に 戻る こと が
            #   ある ため 、 各 tick で 減光 を 再適用 して 暗さ を 維持 する
            #   (減光 中 の とき だけ 作用 。 未 減光 なら 何 も し ない)。
            # v1.13.4: during the wait a screen wake / Photos restart can reset the
            #   brightness, so re-apply the dim on every tick to keep it dimmed
            #   (only while a dim is active; no-op otherwise).
            try:
                reapply_night_brightness()
            except Exception:
                pass

            status = get_backup_status()
            elapsed = time.time() - start

            recent_statuses.append(status)

            if status == "tutorial":
                tutorial_count += 1
                if tutorial_count >= 3:
                    try_close_tutorial()
                    tutorial_count = 0
            else:
                tutorial_count = 0

            if status == "lock_screen":
                lock_count += 1
                if lock_count >= 3:
                    Logger.warning(
                        f"Lock screen persist (x{lock_count})"
                        f" #{recovery_count}: force prepare_ui + restart photos"
                    )
                    recovery_count += 1
                    self._run_recovery_sequence(force_stop_photos=True)
                    lock_count = 0
            else:
                lock_count = 0

            # v1.7.5 ADB disconnected 多発 悪循環 対策:
            #   status=unknown の skip 判定 は _has_gp_line / _gp_extract の 計算 が
            #   必要 な の で 、 い ま は 保留フラグ だけ 立て て DEBUG BAR 処理 の あと で
            #   実際 の unknown_count 判定 を 行う (NameError / UnboundLocalError 防止)。
            # v1.7.5: Defer the status=unknown skip decision until after the DEBUG BAR
            #   (which computes _has_gp_line / _gp_extract) to avoid NameError.
            _unknown_pending = (status == "unknown")
            if not _unknown_pending:
                unknown_count = 0

            if status == "backing_up":
                saw_backing_up = True
                backing_up_count += 1
                complete_streak = 0

                if _stall_limit > 0 and backing_up_count >= _stall_limit:
                    Logger.warning(
                        "Google Photos Backup Stalled: restart UI sequence"
                    )
                    self._run_recovery_sequence(force_stop_photos=True)
                    backing_up_count = 0

            elif status == "waiting":
                saw_waiting = True
                backing_up_count = 0
                complete_streak = 0

            elif status == "preparing":
                # v1.14.2: 転送 直後 の Google フォト 「バックアップ の 準備中 /
                #   フォト ライブラリ を スキャン しています」 は 正常 な 待機 状態 。
                #   unknown と 区別 する こと で 、 毎 tick の 強制 復旧 (scroll +
                #   prepare_ui) と WARNING 連投 を 根本 的 に 防ぐ 。
                #   (旧: unknown 扱い の ため 「unknown streak=N」 が 数十 行 出 て い た)
                #   v1.14.2: The post-transfer "preparing / scanning" screen is a
                #   normal wait. Treating it as its own status prevents per-tick
                #   forced recovery and the "unknown streak=N" WARNING storm.
                saw_preparing = True
                if not preparing_logged:
                    try:
                        Logger.info(tr("bw.log.preparing_detected"))
                    except Exception:
                        pass
                    preparing_logged = True
                backing_up_count = 0
                complete_streak = 0

            else:
                backing_up_count = 0

            if status == "complete":
                saw_complete_ever = True
                saw_complete_count_total += 1
                complete_streak += 1
                if first_complete_ts is None:
                    first_complete_ts = time.time()
            else:
                if status in STACK_TARGET_STATUSES:
                    complete_streak = 0
            # NEW v1.5.4: complete初回出現からの経過秒数を更新
            # NEW v1.5.4: Update seconds elapsed since "complete" first appeared.
            if first_complete_ts is not None:
                complete_elapsed = time.time() - first_complete_ts
            else:
                complete_elapsed = 0.0

            unknown_rate = 0.0
            total_n = len(recent_statuses)
            if total_n > 0:
                unknown_rate = recent_statuses.count("unknown") / float(total_n)
            complete_hits_in_window = recent_statuses.count("complete")
            # NEW: complete_elapsed>0 のとき Status行に表示
            # NEW: Show in the status line when complete_elapsed > 0.
            first_c_display = (
                f"{complete_elapsed:>4.0f}s" if complete_elapsed > 0
                else tr("bw.label.first_c_empty")
            )
            # ============================================================
            # v1.7.4 補足: g_saw_ok / b_streak_ok ... など 12個 の 受理ルールフラグ を
            # 毎 poll で 毎回 再計算 する。これ が 無い と ヒント 行 や Accept 判定 で
            # NameError (未代入 参照) が 発生 し て しまう ため 絶対 必要。
            # v1.7.4: Recompute the 12 acceptance-rule flags on every poll; without this
            # a NameError would occur in the hint line / Accept decision.
            # ============================================================
            g_saw_ok = bool(first_complete_ts is not None)
            g_elapsed_ok = bool(g_saw_ok and complete_elapsed >= FIRST_COMPLETE_ACCEPT_DELAY_SECONDS)
            b_streak_ok = bool(saw_any_valid_progress() and complete_streak >= MIN_COMPLETE_STREAK)
            b_elapsed_ok = bool(elapsed >= MIN_ACCEPT_ELAPSED_SECONDS)
            f_count_ok = bool(saw_complete_count_total >= 2)
            f_elapsed_ok = b_elapsed_ok
            _e_min_hits = max(2, FORCE_ULTRA_MIN_COMPLETE_HITS - 1)
            e_count_ok = bool(complete_hits_in_window >= _e_min_hits)
            e_elapsed_ok = bool(elapsed >= int(MIN_ACCEPT_ELAPSED_SECONDS * 1.5))
            z_saw_ok = bool(saw_complete_ever)
            z_elapsed_ok = bool(elapsed >= ULTRA_SAFE_ELAPSED_SECONDS)
            d_count_ok = bool(complete_hits_in_window >= FORCE_ULTRA_MIN_COMPLETE_HITS)
            d_elapsed_ok = bool(elapsed >= FORCE_ULTRA_ELAPSED_SECONDS)
            # v1.7.4 新規: 詳細 状態 ラベル を 優先 表示。
            # 画面 から 「フォトライブラリ スキャン中」 な ど 明確 な 文言 が 読み取れ て い れ ば
            # 無味乾燥 な unknown_rate 表示 の 代わり に 「〇〇 中 (正常 な 待機 です)」 を 表示 し
            # ユーザー の 不安 を 解消 する。
            # v1.7.4: Prefer a detailed status label. When a clear on-screen phrase
            # is readable, show it instead of the dry unknown_rate to reassure the user.
            try:
                detailed = get_last_detailed_status()
            except Exception:
                detailed = None
            if detailed:
                label_key = f"bw.label.{detailed}"
                try:
                    status_label = tr(label_key)
                except Exception:
                    status_label = None
                if not status_label or (isinstance(status_label, str) and (not status_label or status_label.startswith("bw.label."))):
                    status_label = None
            else:
                status_label = None
            if status_label is None:
                status_label = tr("bw.label.unknown_rate").format(unk_pct=int(round(unknown_rate * 100)))
            # v1.7.5: LOG_VERBOSITY に 応じ て Status / recent 履歴 行 の 表示 を 切り替え 。
            #   full の とき は 旧 通り 完全 な debug 情報 (status header + recent) 両方 表示 。
            #   standard / minimal の とき は これら 開発 向け 詳細 行 は 完全 非表示 と し 、
            #   画面 を スッキリ さ せ て 次 の GP 進捗 行 (▶ GP:) を 見 やすく する 。
            # v1.7.5: Toggle the Status / recent history lines based on LOG_VERBOSITY.
            #   In "full" show both as before; in standard/minimal hide them entirely
            #   to keep the screen clean and highlight the GP progress line.
            try:
                from nas2gp.core.settings import LOG_VERBOSITY as _BWLV
                _bwlv = (_BWLV or "standard").lower() if isinstance(_BWLV, str) else "standard"
            except Exception:
                _bwlv = "standard"
            if _bwlv == "full":
                print(tr("bw.status.header").format(
                    status=status, elapsed=elapsed, timeout=_idle_timeout,
                    streak=complete_streak, total_c=saw_complete_count_total,
                    first_c=first_c_display,
                    recent=complete_hits_in_window, window=RECENT_WINDOW,
                    saw_b=int(saw_backing_up), saw_w=int(saw_waiting),
                    status_label=status_label,
                ))
                # デバッグ用: 過去 N ステータス履歴を一行にまとめて表示 (圧縮 10 文字 を 超え たら 古い 部分 を 省略)
                # Debug: Show the past N statuses on one line (trim older ones past 12).
                codes = {
                    "complete": "c",
                    "backing_up": "b",
                    "waiting": "w",
                    "preparing": "p",
                    "lock_screen": "l",
                    "tutorial": "t",
                    "unknown": "u",
                }
                _last_list = list(recent_statuses)
                if len(_last_list) > 12:
                    _dbg_last = "".join(codes.get(s, "?") for s in _last_list[-12:])
                    _dbg_last = f"...{_dbg_last}"
                else:
                    _dbg_last = "".join(codes.get(s, "?") for s in _last_list)
                print(tr("bw.recent.line").format(recent=_dbg_last))

            # ===== DEBUG BAR (v1.7.4 簡易版 → v1.7.5 LOG_VERBOSITY 対応 + GP 行 分割
            # ===== DEBUG BAR (v1.7.4 simple -> v1.7.5 LOG_VERBOSITY aware, GP line split)
            # v1.11.2: 待機理由 (受理条件 ヒント) は 全廃 し 、 GP 進捗 行 のみ 表示 する 。
            # v1.11.2: The wait reason (acceptance hint) was removed; only the GP
            #   progress line is printed.
            _gp_line = ""
            # v1.7.5 新規: Google Photos の 画面 から 直接 「残り枚数 / 残り時間」 が 取れ て い れ ば 最優先 表示。
            # v1.7.5: If the remaining items / time is directly readable from the
            # Google Photos screen, show it with top priority.
            _gp_extract = None
            try:
                # 既存 の read_texts() キャッシュ を 再 使う ため texts=None 省略
                # texts=None omits it so the existing read_texts() cache is reused.
                _gp_extract = extract_google_photos_progress()
            except Exception:
                _gp_extract = None
            _has_gp_line = False
            if isinstance(_gp_extract, dict):
                _gp_items = _gp_extract.get("items_total")
                _gp_eta = _gp_extract.get("eta_seconds")
                _gp_eta_text = _gp_extract.get("eta_text")
                _gp_detail = _gp_extract.get("detail_text")
                _gp_parts = []
                # ★ v1.7.5 2 重 ガード: status == "complete" の 時 は
                #  万が一 extract が 「残り X 枚 / 残り Y 分」 を 誤抽出 し て い て も
                #  DEBUG BAR に は 一切 表示 せ ず 、 detail_text (完了 説明 文) だけ を 使う 。
                #  これ に より 「残り823枚 / バックアップが完了しました」 の 矛盾 表示 を 根絶 。
                # ★ v1.7.5 double guard: when status == "complete" never show
                #  "X remaining" extractions in the DEBUG BAR; use detail_text only,
                #  eliminating contradictory "823 remaining / backup complete" output.
                _complete_mode = (status == "complete")
                if not _complete_mode and _gp_items:
                    try:
                        _gp_parts.append(tr("log.gp.remain_items").format(n=int(_gp_items)))
                    except Exception:
                        pass
                if not _complete_mode:
                    if _gp_eta_text:
                        _gp_parts.append(str(_gp_eta_text))
                    elif _gp_eta:
                        try:
                            _s = int(_gp_eta or 0)
                            if _s >= 3600:
                                h = _s // 3600; m = (_s % 3600) // 60
                                _gp_parts.append(tr("log.gp.eta_hm").format(h=h, m=m))
                            elif _s >= 60:
                                _gp_parts.append(tr("log.gp.eta_m").format(m=_s // 60))
                            else:
                                _gp_parts.append(tr("log.gp.eta_s").format(s=_s))
                        except Exception:
                            pass
                if _gp_detail:
                    _gp_parts.append(str(_gp_detail)[:60])
                if _gp_parts:
                    _gp_line = "Google Photos: " + " / ".join(_gp_parts)
                    _has_gp_line = True
            # v1.11.2: 受理条件 ヒント 行 (旧 「待機理由」) は ノイズ の ため 全廃 し 、
            #   GP 進捗 行 のみ を 表示 する 。
            # v1.11.2: The acceptance-hint line (formerly "wait reason") was removed
            #   entirely as noise; only the GP progress line is shown.
            if _gp_line:
                print("  ▶ " + _gp_line)
            # ================ END DEBUG BAR ================

            # v1.7.15: 進捗 検出 (無進捗 タイマー の リセット 判定)。
            #   残り 枚数 の 減少 / ETA の 短縮 / complete の 新規 検出 の いずれか が
            #   あれ ば 「前進 し て いる」 と みなし 、 タイムアウト を 延長 する 。
            #   これ に より 大容量 ファイル の 長時間 バックアップ を 誤って 打ち切ら ない 。
            # v1.7.15: Progress detection (resets the no-progress timer). A drop in
            # remaining items, a shorter ETA, or a new complete counts as progress.
            _progress_now = False
            _rem_now = None
            _eta_now = None
            try:
                if isinstance(_gp_extract, dict):
                    _rem_now = _gp_extract.get("items_total")
                    _eta_now = _gp_extract.get("eta_seconds")
            except Exception:
                _rem_now = None
                _eta_now = None
            if _rem_now is not None:
                try:
                    _rem_i = int(_rem_now)
                    if last_remaining is not None and _rem_i < last_remaining:
                        _progress_now = True
                    last_remaining = _rem_i
                except Exception:
                    pass
            if _eta_now is not None:
                try:
                    _eta_i = int(_eta_now)
                    if last_eta is not None and _eta_i < last_eta:
                        _progress_now = True
                    last_eta = _eta_i
                except Exception:
                    pass
            if saw_complete_count_total > prev_complete_total:
                _progress_now = True
            prev_complete_total = saw_complete_count_total
            if _progress_now:
                last_progress_ts = time.time()


            # v1.7.5 ADB disconnected 多発 悪循環 対策 (遅延 判定 パート):
            #   _has_gp_line / _gp_extract の 代入 が DEBUG BAR で 完了 し た あと で
            #   unknown_count を 進める か skip する か の 判定 を 行う 。
            # v1.7.5 measure against repeated ADB disconnects (deferred part): after
            #   the DEBUG BAR assigns _has_gp_line / _gp_extract, decide whether to
            #   advance or skip unknown_count.
            if _unknown_pending:
                _skip_unknown_count = False
                try:
                    if _has_gp_line and isinstance(_gp_extract, dict):
                        _d = (_gp_extract.get("detail_text") or "") + (_gp_line or "")
                        import re as _re2x
                        if _gp_extract.get("items_total") is None and (_gp_extract.get("detail_text") or "").strip():
                            _skip_unknown_count = True
                        else:
                            if _re2x.search(r"(バックアップ.*(?:完了|最新|しました)|アップロード.*(?:完了|しました)|Back(?:ing|ed) up.*complete|Backup.*(?:complete|up to date|All .+ backed up)", _d, _re2x.I):
                                _skip_unknown_count = True
                except Exception:
                    _skip_unknown_count = False
                if not _skip_unknown_count:
                    unknown_count += 1
                    # v1.14.2: 停滞 しきい値 は backing_up 停滞 と 同じ 実効 値 を 使う
                    #   (旧: 3 固定 で 設計 が 二重 化 し て い た)。
                    # v1.14.2: Use the same effective threshold as the backing_up stall
                    #   (the old hard-coded 3 duplicated the stall-limit design).
                    _recover_limit = _stall_limit if _stall_limit > 0 else 3
                    if unknown_count >= _recover_limit:
                        unknown_count = 0
                        recovery_count += 1
                        # v1.14.2: 交互 に 「画面 復帰 のみ」 と 「Photos 再起動」 を 行う 。
                        #   旧 コード の unknown_count % 2 は 直後 に 0 へ リセット さ れ る ため
                        #   常に False に な り 、 Photos 再起動 が 一度 も 実行 さ れ な い 死んだ 分岐 だっ た 。
                        # v1.14.2: Alternate between a plain UI resume and a Photos restart.
                        #   The old unknown_count % 2 was always False (reset to 0 right
                        #   after), so the restart branch was dead code.
                        _force_stop = bool(recovery_count % 2 == 0)
                        _recover_msg = (
                            f"Google Photos Recovery (unknown x{_recover_limit}) "
                            f"#{recovery_count} force_stop={_force_stop}"
                        )
                        # v1.14.2: 同じ 待機 が 続く 間 の 連投 を 防ぐ ため 、 1 回 目 だけ WARNING 。
                        # v1.14.2: Only the first recovery of the episode is a WARNING
                        #   (prevents a continuous WARNING stream while waiting).
                        if recovery_count == 1:
                            Logger.warning(_recover_msg)
                        else:
                            Logger.info(_recover_msg)
                        self._run_recovery_sequence(force_stop_photos=_force_stop)

            # v1.7.15: 定期 ハートビート ([ハートビート] ...) は ログ ノイズ の ため 廃止 。
            #   待機中 で ある こと は 上記 DEBUG BAR の GP 進捗 行 で 十分 に 把握 できる 。
            # v1.7.15: Removed the periodic heartbeat log (noise); the DEBUG BAR GP
            #   progress line conveys the waiting state well enough.


            # v1.7.15: 受理 (return True) 直前 に 「完了」 tick を 発行 する ヘルパー。
            #   これ が 無い と 、 受理 した 回 は 下 の 定期 publish に 到達 せ ず
            #   Web UI が 最後 の 「残り1枚」 表示 の まま 更新 さ れ な い。
            # v1.7.15: Helper that emits a final "complete" tick right before
            # accepting; without it the Web UI keeps showing the last progress state.
            def _finalize_complete():
                # v1.14.2: 受理 (return) 直前 は 画面 を 見る 必要 が 無い ため 、 ここ で OFF に
                #   戻す 。 これ が 無い と 最後 の 状態 確認 (wakeup) で 点灯 した まま 、
                #   後続 の 削除 工程 へ 点灯 が 残っ て しまう 。
                # v1.14.2: no screen is needed right before acceptance, so turn it off
                #   here; otherwise the light stays on from the last status check into
                #   the following delete stage.
                try:
                    keep_screen_off()
                except Exception:
                    pass
                try:
                    _events.publish(_events.BACKUP_RULES_TICK, {
                        "gp": {
                            "items": 0,
                            "eta_seconds": 0,
                            "eta_text": "",
                            "detail_text": "",
                            "complete": True,
                        }
                    })
                except Exception:
                    pass

            # --- A. 厳格: backing_up → complete の遷移確認
            # --- A. Strict: confirm the backing_up -> complete transition
            if status == "complete" and saw_backing_up:
                self._log_accept_simple("A", tr("log.gp.rule_a"), elapsed, complete_streak, saw_complete_count_total, complete_hits_in_window, RECENT_WINDOW, saw_backing_up, saw_waiting)
                print(tr("backup.complete"))
                _finalize_complete()
                return True, recovery_count

            # --- G. ★新・最速ルート (v1.5.4): completeが1回でも出てから FIRST_DELAY秒経過
            #        (MIN_ACCEPT のような loop開始からの経過ではなく complete出現から計るので無駄なし)
            #        (measured from the complete, not from loop start, so no wasted wait)
            if g_saw_ok and g_elapsed_ok:
                self._log_accept_simple("G", tr("log.gp.rule_g").format(delay=FIRST_COMPLETE_ACCEPT_DELAY_SECONDS), elapsed, complete_streak, saw_complete_count_total, complete_hits_in_window, RECENT_WINDOW, saw_backing_up, saw_waiting, first_sec=complete_elapsed)
                print(tr("backup.complete"))
                _finalize_complete()
                return True, recovery_count

            # --- B. 緩和: streak >= 1 + MIN_ACCEPT経過 (標準ルート, MIN_ACCEPT=15sに短縮)
            # --- B. Relaxed: streak >= 1 and MIN_ACCEPT elapsed (standard route)
            if b_streak_ok and b_elapsed_ok:
                self._log_accept_simple("B", tr("log.gp.rule_b"), elapsed, complete_streak, saw_complete_count_total, complete_hits_in_window, RECENT_WINDOW, saw_backing_up, saw_waiting)
                print(tr("backup.complete"))
                _finalize_complete()
                return True, recovery_count

            # --- F. TOTAL COUNT ルール: total_c>=2 + MIN_ACCEPT経過 (旧 total>=3 から緩和)
            # --- F. Total-count rule: total_c >= 2 and MIN_ACCEPT elapsed.
            if f_count_ok and f_elapsed_ok:
                self._log_accept_simple("F", tr("log.gp.rule_f"), elapsed, complete_streak, saw_complete_count_total, complete_hits_in_window, RECENT_WINDOW, saw_backing_up, saw_waiting, level="warning")
                print(tr("backup.complete"))
                _finalize_complete()
                return True, recovery_count

            # --- E. complete回数ベース: recent window内のcomplete回数で判定
            # --- E. Complete-count based: judge by complete hits within the recent window.
            if e_elapsed_ok and e_count_ok and saw_complete_ever:
                self._log_accept_simple("E", tr("log.gp.rule_e"), elapsed, complete_streak, saw_complete_count_total, complete_hits_in_window, RECENT_WINDOW, saw_backing_up, saw_waiting, level="warning")
                print(tr("backup.complete"))
                _finalize_complete()
                return True, recovery_count

            # --- Z. Ultra-Safe: 一度でも complete 観測済み + ULTRA_SAFE経過 (旧102s→新40s)
            # --- Z. Ultra-Safe: at least one complete seen and ULTRA_SAFE elapsed.
            if z_saw_ok and z_elapsed_ok:
                self._log_accept_simple("Z", tr("log.gp.rule_z"), elapsed, complete_streak, saw_complete_count_total, complete_hits_in_window, RECENT_WINDOW, saw_backing_up, saw_waiting, level="warning")
                print(tr("backup.complete"))
                _finalize_complete()
                return True, recovery_count

            # --- D. Force Ultra: 時間+回数ベースの強制受理 (最強の保険, 旧120s→新80s)
            # --- D. Force Ultra: forced acceptance by time + hit count (strongest fallback).
            if d_elapsed_ok and d_count_ok:
                self._log_accept_simple("D", tr("log.gp.rule_d"), elapsed, complete_streak, saw_complete_count_total, complete_hits_in_window, RECENT_WINDOW, saw_backing_up, saw_waiting, level="warning")
                print(tr("backup.complete"))
                _finalize_complete()
                return True, recovery_count

            # v1.7.15: 打ち切り 判定 を 「全体 経過」 から 「無進捗 の 継続」 に 変更 。
            #   _idle が timeout を 超え た とき のみ 打ち切る 。 加えて 無限 待機 防止 の
            #   絶対上限 (BACKUP_ABSOLUTE_MAX_SECONDS) も 併用 する 。
            # v1.7.15: Abort decision changed from total elapsed to continuous
            # no-progress; also uses an absolute upper bound to prevent endless waits.
            _idle = time.time() - last_progress_ts
            _timeout_hit = bool(_idle > _idle_timeout or elapsed > _abs_max)
            if _timeout_hit:

                # --- C. 最終保険 (timeout時多数決)
                # --- C. Last-resort (timeout quorum vote)
                total_recent = len(recent_statuses)
                complete_recent = recent_statuses.count("complete")
                quorum_ok = (
                    total_recent > 0
                    and complete_recent / max(1, total_recent) >= COMPLETE_QUORUM_RATIO
                )
                if quorum_ok:
                    Logger.warning(
                        tr("bw.log.accept_C").format(
                            recent_c=complete_recent, total=total_recent, elapsed=elapsed,
                        )
                    )
                    print(tr("backup.complete"))
                    _finalize_complete()
                    return True, recovery_count

                print(tr("backup.timeout"))
                Logger.warning(
                    tr("bw.log.timeout_false").format(
                        elapsed=elapsed, rc=recovery_count, uc=unknown_count, idle=_idle,
                    )
                )
                return False, recovery_count

            try:
                e_min_hits_val = max(2, FORCE_ULTRA_MIN_COMPLETE_HITS - 1)
                state = {
                    "A": {"priority": 1, "desc": "strict backing->complete", "ok": bool(status == 'complete' and saw_backing_up), "current_val": f"status={status},saw_b={int(saw_backing_up)}", "threshold": 1},
                    "G": {"priority": 2, "desc": "first complete + DELAY", "ok": bool(g_saw_ok and g_elapsed_ok), "current_val": f"{complete_elapsed:.0f}s", "threshold": FIRST_COMPLETE_ACCEPT_DELAY_SECONDS},
                    "B": {"priority": 3, "desc": "streak + MIN_ACCEPT", "ok": bool(b_streak_ok and b_elapsed_ok), "current_val": f"streak={complete_streak},el={elapsed:.0f}s", "threshold": f">={MIN_COMPLETE_STREAK},>={MIN_ACCEPT_ELAPSED_SECONDS}s"},
                    "F": {"priority": 4, "desc": "total_c >=2 + MIN", "ok": bool(f_count_ok and f_elapsed_ok), "current_val": f"total_c={saw_complete_count_total},el={elapsed:.0f}s", "threshold": f">=2,>={MIN_ACCEPT_ELAPSED_SECONDS}s"},
                    "E": {"priority": 5, "desc": "window hits + 1.5xMIN", "ok": bool(e_count_ok and e_elapsed_ok), "current_val": f"recent_c={complete_hits_in_window},el={elapsed:.0f}s", "threshold": f">={e_min_hits_val},>={int(MIN_ACCEPT_ELAPSED_SECONDS * 1.5)}s"},
                    "Z": {"priority": 6, "desc": "UltraSafe 40s", "ok": bool(z_saw_ok and z_elapsed_ok), "current_val": f"saw_c={int(z_saw_ok)},el={elapsed:.0f}s", "threshold": f">=40s"},
                    "D": {"priority": 7, "desc": "ForceUltra 80s", "ok": bool(d_count_ok and d_elapsed_ok), "current_val": f"recent_c={complete_hits_in_window},el={elapsed:.0f}s", "threshold": f">={FORCE_ULTRA_MIN_COMPLETE_HITS},>={FORCE_ULTRA_ELAPSED_SECONDS}s"},
                    "C": {"priority": 8, "desc": "Timeout Quorum 30%", "ok": bool(_timeout_hit and ('complete_recent' in locals()) and quorum_ok) if 'quorum_ok' in locals() else False, "current_val": f"quorum={complete_recent if _timeout_hit and 'complete_recent' in locals() else 0}/{total_recent if _timeout_hit and 'total_recent' in locals() else 0}", "threshold": f">={COMPLETE_QUORUM_RATIO * 100:.0f}%"},
                }
                # v1.7.14: Google Photos 画面 から 取得 し た 「残り枚数 / 残り時間」 を
                #   コンソール print だけで なく イベント に も 載せ 、 Web UI 等 の 表示 へ 伝播 さ せる 。
                # v1.7.14: Also attach the remaining items / time to the event (not only
                # the console print) so it propagates to the Web UI.
                try:
                    if isinstance(_gp_extract, dict):
                        state["gp"] = {
                            "items": _gp_extract.get("items_total"),
                            "eta_seconds": _gp_extract.get("eta_seconds"),
                            "eta_text": _gp_extract.get("eta_text"),
                            "detail_text": _gp_extract.get("detail_text"),
                            "complete": bool(_complete_mode),
                        }
                except Exception:
                    pass
                _events.publish(_events.BACKUP_RULES_TICK, state)
            except Exception:
                pass

            # v1.13.9: 待機 ループ で も 電池 を 監視 する (従来 は この ループ に 電池
            #   チェック が 無く 、 待機 中 に 端末 が 電源 落ち し た)。
            # v1.14.2: 残量 が 閾値 未満 の とき は 即 停止 せず 回復 まで 待機 する
            #   (転送 フェーズ と 同じ 挙動)。 危険域 / 待機 上限 超過 で 保護 停止。
            # v1.13.9: Watch the battery inside the wait loop as well.
            # v1.14.2: Below the threshold we wait for recovery instead of stopping
            #   immediately (same as the transfer phase); a critical level or an
            #   exceeded wait limit results in a protection stop.
            try:
                _bat_status, _bat_waited = self._battery_gate()
            except Exception:
                _bat_status, _bat_waited = "OK", 0.0
            if _bat_status != "OK":
                # この バッチ を バックアップ 未確認 (False) と し て 返す 。
                return False, recovery_count
            if _bat_waited > 0:
                # 電池 回復 待ち は Google Photos の 停滞 で は ない ため 、
                #   無進捗 タイマー を リセット する (復帰 直後 の 誤 タイムアウト 防止)。
                last_progress_ts = time.time()

            # v1.14.2: 状態 確認 が 終わっ た ので 、 次 の ポーリング まで 画面 を OFF に
            #   する (UI 不要 区間 の 消灯)。 Google Photos の アップロード は 画面 OFF の
            #   まま でも 継続 する (夜間 の 充電 バックアップ と 同じ 挙動)。
            #   状態 確認 の 直後 は 画面 が 点い て いる ため 、 スロットル に 関係 なく 必ず
            #   消灯 する (force=True)。 これ を 省く と ポーリング 周期 の 間 点灯 が 残る 。
            # v1.14.2: The status fetch is done, so turn the screen off until the next
            #   poll (the UI is not needed). Google Photos keeps uploading with the
            #   screen off, just like a night-time charging backup. Right after a fetch
            #   the screen is lit, so force the off key regardless of the throttle.
            try:
                keep_screen_off(force=True)
            except Exception:
                pass

            time.sleep(interval)

    def _run_recovery_sequence(self, force_stop_photos=False):
        prepare_ui()
        try:
            from nas2gp.device.device import Device as _Px
            px = _Px()
            if force_stop_photos:
                px.restart_google_photos()
            px.ensure_adb_alive()
        except Exception:
            pass

    def delete_uploaded_files(self, filenames):
        """
        アップロード済みファイルを端末から削除する

        v1.11.3: delete_file() が 三値 (True=削除 / False=失敗 / None=端末 に 存在 せず) を
        返す よう に なっ た ため 、 存在 し なかっ た もの は 削除 済み と し て 返さ ない
        (DB を 誤っ て DONE 化 し ない)。戻り値 は 実際 に 削除 でき た ファイル名 の リスト 。

        Deletes already-uploaded files from the device. v1.11.3: delete_file() now returns
        a tri-state; files that were not present are NOT reported as deleted (so the DB is
        not wrongly marked DONE). Returns the list of files actually removed.
        """

        from nas2gp.util import print_phase

        print_phase(tr("delete.start"))

        deleted = 0
        skipped_absent = 0
        failed = 0
        deleted_files = []

        for filename in filenames:

            try:
                _res = self.device.delete_file(filename)
            except Exception:
                _res = False

            if _res is True:

                print(f"DELETE : {filename}")
                deleted += 1
                deleted_files.append(filename)

            elif _res is None:

                # v1.11.3: 端末 に 存在 し なかっ た (作業フォルダ 違い など)。DB は DONE に しない 。
                skipped_absent += 1
                print(f"SKIP   : {filename} (not on device)")

            else:

                failed += 1
                print(f"FAILED : {filename}")

        print()
        print(f"Deleted : {deleted}/{len(filenames)}")
        if skipped_absent or failed:
            print(f"Skipped(absent) : {skipped_absent}  Failed : {failed}")

        return deleted_files

    @staticmethod
    def _log_accept(rule, elapsed):
        Logger.info(f"Accept complete via rule [{rule}] elapsed={elapsed:.0f}s")

    @staticmethod
    def _log_accept_simple(rule_id, rule_label, elapsed, streak, total_c, recent_c, recent_w, saw_b, saw_w, level="info", first_sec=None):
        msg = (
            tr("log.gp.accept_simple").format(rule_id=rule_id, rule_label=rule_label, elapsed=int(elapsed), streak=int(streak), total_c=int(total_c), recent_c=int(recent_c), recent_w=int(recent_w), saw_b=int(saw_b), saw_w=int(saw_w))
        )
        if first_sec is not None:
            msg += tr("log.gp.accept_simple_first_sec").format(first_sec=int(first_sec))
        if level == "warning":
            Logger.warning(msg)
        else:
            Logger.info(msg)
