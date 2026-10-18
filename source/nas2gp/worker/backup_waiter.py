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
)
from nas2gp.core.logger import Logger
from nas2gp.core.i18n import tr
from nas2gp.core import events as _events


class UploadWorker:

    def __init__(self):

        self.device = Device()

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

        print_phase(tr("backup.waiting"))

        start = time.time()

        unknown_count = 0
        backing_up_count = 0
        tutorial_count = 0

        saw_backing_up = False
        saw_waiting = False
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
            return saw_backing_up or saw_waiting

        lock_count = 0

        while True:

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

                if backing_up_count >= BACKUP_STALLED_LIMIT:
                    Logger.warning(
                        "Google Photos Backup Stalled: restart UI sequence"
                    )
                    self._run_recovery_sequence(force_stop_photos=True)
                    backing_up_count = 0

            elif status == "waiting":
                saw_waiting = True
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
                    status=status, elapsed=elapsed, timeout=timeout,
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
            # v1.7.5 追加:
            #   (1) 見やす さ 優先 で 「GP 進捗 行 と 「待機理由/受理条件」 を _別 行 に 分割 し て 表示 する (改行 入れる)。
            #   (2) standard/minimal で は Status / recent を 削除 し て い 。
            # v1.7.5: (1) Split the GP progress line and the wait-reason line for
            #   readability. (2) Remove Status / recent lines in standard/minimal.
            try:
                from nas2gp.core.settings import LOG_VERBOSITY as _LV_STR
                _lv = (_LV_STR or "standard").lower() if isinstance(_LV_STR, str) else "standard"
            except Exception:
                _lv = "standard"
            _is_minimal = (_lv == "minimal")
            _gp_line = ""
            _other_hints = []
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
            # v1.7.15: 受理条件 (最速 G / 標準 B / 最終保険 Z) の 内部 ヒント は
            #   ノイズ の ため 非表示 に する 。 ユーザー 向け の 「待機理由」 のみ 残す 。
            # v1.7.15: Hide internal acceptance-rule hints (fastest G / standard B /
            # last-resort Z) as noise; keep only the user-facing wait reason.
            if not (g_saw_ok and g_elapsed_ok) and not g_saw_ok:
                _other_hints.append(tr("log.gp.wait_reason"))
            if _gp_line or _other_hints:
                # ★ v1.7.5 見 やす さ 改善: 「GP 進捗」 と 「ヒント/受理条件」 を 改行 で 分離 。
                #   (旧来: 1 行 に / 区切り で 全部 乗せ て い た ため 横長 に なり 見 にくかっ た)
                # ★ v1.7.5 readability: Split the GP progress and the hint/acceptance
                #   fields with a line break (previously all on one long line).
                if _is_minimal:
                    _max_other = 1 if _has_gp_line else 2
                    _shown_other = _other_hints[:_max_other]
                    if _gp_line:
                        print("  ▶ " + _gp_line)
                    if _shown_other:
                        print("    " + " / ".join(_shown_other))
                else:
                    # standard / full: GP は 1 行 目 、 ヒント は 最大 3 件 を 2 行 目 以降 に 分割 表示
                    # standard / full: GP on line 1, up to 3 hints split onto the following lines.
                    if _gp_line:
                        print("  ▶ " + _gp_line)
                    if _other_hints:
                        # 最初 の 3 件 を 優先 し て さら に 2 行 に 分け て 表示 (見 やす さ 優先)
                        # Show the first 3 hints split over two lines for readability.
                        first_other = _other_hints[0]
                        rest = _other_hints[1:3]
                        print("    " + first_other)
                        if rest:
                            print("    " + " / ".join(rest))
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
                    if unknown_count >= 3:
                        Logger.warning(
                            f"Google Photos Recovery (unknown x{unknown_count}) #{recovery_count}"
                        )
                        recovery_count += 1
                        self._run_recovery_sequence(force_stop_photos=(unknown_count % 2 == 0))
                        unknown_count = 0

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
            _timeout_hit = bool(_idle > timeout or elapsed > _abs_max)
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

        Deletes already-uploaded files from the device.
        """

        from nas2gp.util import print_phase

        print_phase(tr("delete.start"))

        deleted = 0
        deleted_files = []

        for filename in filenames:

            if self.device.delete_file(filename):

                print(f"DELETE : {filename}")
                deleted += 1
                deleted_files.append(filename)

            else:

                print(f"FAILED : {filename}")

        print()
        print(f"Deleted : {deleted}/{len(filenames)}")

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
