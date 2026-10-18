# ============================================
# FILE: nas2gp/device.py
# ============================================

import time
import shlex
from pathlib import PurePosixPath

from nas2gp.device.adb import ADB
from nas2gp.core.i18n import tr
from nas2gp.core.settings import (
    DEVICE_WORK_DIR,
    ADB_RECONNECT_INTERVAL,
)


def _sh_quote(posix_path):
    s = str(posix_path or "").strip()
    if not s:
        return "''"
    return shlex.quote(s)


class Device:

    @property
    def WORK_DIR(self):
        """作業フォルダ (端末 側 の 転送先 兼 削除 対象) を settings から 動的 に 解決 する。

        v1.8.0: 端末 ページ から 作業フォルダ を 変更 できる よう に し た ため 、
        import 時 の 静的 コピー を やめ 、 参照 の たび に settings を 見る 。
        未設定 時 は 既定 値 DEVICE_WORK_DIR を 返す 。

        Resolve the working folder (device-side transfer + delete target) dynamically from
        settings. v1.8.0: since the working folder is now changeable from the device page,
        the import-time static copy was replaced with a per-access read; falls back to
        DEVICE_WORK_DIR when unset.
        """
        try:
            from nas2gp.core import settings as _s
            v = str(getattr(_s, "DEVICE_WORK_DIR", "") or "").strip()
            if v:
                return v
        except Exception:
            pass
        return DEVICE_WORK_DIR

    def camera_files(self):

        return set(
            ADB.list_dir(self.WORK_DIR)
        )

    def upload(self, local_path, filename):

        remote = f"{self.WORK_DIR}/{filename}"

        return ADB.push(
            local_path,
            remote,
        )

    def reboot_device(self, wait_seconds_after_reboot=45.0):
        """
        端末 本体 を 再起動 (adb reboot) する 。
        ただし ADB 接続 が 切れ て いる 場合は コマンド が 届か ない ため False を 返し 、 呼び出し側 で ユーザー 介入 ダイアログ へ 委ねる 。
        成功 時 は 再起動 後 の ADB 再接続 まで wait_seconds_after_reboot 秒 待機 し 、 復旧 確認 でき た 場合 True を 返す 。
        """
        from nas2gp.core.logger import Logger
        try:
            if not self.ensure_adb_alive(aggressive=True, shell_probe=True, publish_unrecoverable=False):
                Logger.warning(tr("log.device.reboot_adb_ng"))
                return False
        except Exception:
            return False
        try:
            if not ADB.has_device():
                Logger.warning(tr("log.device.reboot_no_device"))
                return False
        except Exception:
            return False
        try:
            Logger.info(tr("log.device.reboot_issue"))
            try:
                ADB.shell("reboot")
            except Exception:
                pass
            try:
                import time as _rbt
                wait_total = max(15.0, float(wait_seconds_after_reboot or 0))
                _rbt_t0 = _rbt.time()
                _first_wait = min(15.0, wait_total * 0.5)
                _rbt.sleep(_first_wait)
                remain = max(0.0, wait_total - (_rbt.time() - _rbt_t0))
                step = 3.0
                recovered = False
                while remain > 0:
                    _rbt.sleep(min(step, remain))
                    remain = max(0.0, wait_total - (_rbt.time() - _rbt_t0))
                    try:
                        if ADB.has_device():
                            recovered = True
                            break
                    except Exception:
                        continue
                if recovered:
                    try:
                        ok2 = bool(self.ensure_adb_alive(aggressive=False, shell_probe=True, publish_unrecoverable=False))
                        recovered = ok2
                    except Exception:
                        pass
                if recovered:
                    Logger.info(tr("log.device.reboot_ok").format(elapsed=round(_rbt.time()-_rbt_t0,1)))
                    return True
                Logger.warning(tr("log.device.reboot_ng").format(elapsed=round(_rbt.time()-_rbt_t0,1)))
                return False
            except Exception as e:
                Logger.warning(tr("log.device.reboot_wait_exc").format(exc_type=type(e).__name__, exc=e))
                return False
        except Exception as e:
            Logger.error(f"[device] reboot_device FAILED: {type(e).__name__}: {e}")
            return False


    def scan_media(self, filename):
        """
        MediaScannerへ登録する (単体)
        """
        raw_path = f"{self.WORK_DIR}/{str(filename or '')}"
        # shlex.quote で 安全 化 し た パス から URI 化 (括弧 / 空白 / $ 等 対応)
        # (quote が 単一引用符 で 囲わ れる ため URI 組立 時 は 外す 。 quote し て ない 特殊 ケース は 生 の まま)
        # Build the URI from the shlex.quote-sanitized path (handles brackets / spaces / $ etc.)
        # (strip the surrounding single quotes during URI assembly; leave unquoted special cases as-is)
        q = _sh_quote(raw_path)
        if q.startswith("'") and q.endswith("'"):
            uri_suffix = q[1:-1]
        else:
            uri_suffix = q
        script = (
            'am broadcast '
            '-a android.intent.action.MEDIA_SCANNER_SCAN_FILE '
            '-d "file://' + uri_suffix + '" '
            '>/dev/null 2>&1 ; echo DONE'
        )
        return ADB.shell("sh", "-c", script)

    def scan_media_batch(self, filenames):
        """
        MediaScanner を バッチで 1 回の shell コマンドでまとめて登録する。
        ただし ファイル名に非ASCII (日本語などマルチバイト) が1文字でも含まれる場合 / 空 の場合 は
        自動的に 旧来の 1件ずつ個別実行 へ フォールバック する (エンコーディング不整合 防止)。

        v1.7.4 修正: 旧 for f in 'a' 'b' 'c'; do ... done は Android の toybox sh に おいて
                     ファイル名 側 の シングルクォート と sh -c 引数 の シングルクォート が 競合し、
                    「syntax error: unexpected 'do'」 を 引き起こし て い た。
                     これ を 完全 に 廃止 し、すべて の ケース で 改行 区切り の
                    一時 スクリプト 方式 (printf '%s\\n' + while read) を 使用 する。
        """
        if not filenames:
            return True
        # ファイル表示 を 改行 付き で 先に 出力 (v1.7.4 UI改善)
        # Print the file list with line breaks first (v1.7.4 UI improvement)
        try:
            from nas2gp.core.util import print_step as _ps
            _ps(f"Media Scanner target files: {len(filenames)}")
            # 30件 目安 で 代表 表示 (全部出すと長いので 省略)
            # Show a representative preview of ~30 entries (the full list is omitted to keep it short)
            preview = list(filenames[:12])
            for fn in preview:
                print(f"  - {fn}")
            rest = len(filenames) - len(preview)
            if rest > 0:
                print(f"  - ... and {rest} more files")
        except Exception:
            pass
        has_multibyte = False
        try:
            for fn in filenames:
                s = fn or ""
                for ch in s:
                    if ord(ch) > 127:
                        has_multibyte = True
                        break
                if has_multibyte:
                    break
        except Exception:
            has_multibyte = True
        if has_multibyte or len(filenames) >= 50:
            # マルチバイト 含む か 50 件 以上 なら 個別 実行 へ フォールバック (ADB shell コマンド長 制限 回避)
            # Fall back to per-file execution if multibyte names or 50+ files (avoid ADB shell command length limits)
            ok_all = True
            for fn in filenames:
                if not self.scan_media(fn):
                    ok_all = False
            return ok_all
        try:
            # v1.7.4 改修: while read 方式。改行 で 安全 に 区切る ため シングルクォート の 競合 は 起こり え ない。
            # v1.7.4 change: while-read style. Newline separation avoids single-quote conflicts.
            escaped_filenames = []
            for fn in filenames:
                # printf 内 の % と \ だけ エスケープ (残り は 改行 区切り な ので 安全)
                # Escape only % and \ inside printf (the rest is newline-separated, so safe)
                s = (fn or "").replace("\\", "\\\\").replace("%", "%%")
                escaped_filenames.append(s)
            printf_part = "printf '%s\\n' " + " ".join(f"'{s}'" for s in escaped_filenames)
            # while read 中 の file:// URI は read 変数 経由 で 組み立て (特殊文字 を 直接 printf 埋め込み せ ず 済む)
            # Build the file:// URI from the read variable inside while-read (avoids embedding special chars directly in printf)
            work_prefix = str(self.WORK_DIR).rstrip("/")
            script = (
                f"{printf_part} | while IFS= read -r f; do "
                f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
                f"-d \"file://{work_prefix}/$f\" >/dev/null 2>&1; "
                f"done"
            )
            return ADB.shell("sh", "-c", script)
        except Exception as e:
            from nas2gp.core.logger import Logger
            Logger.warning(f"scan_media_batch failed (fallback to individual): {e}")
            ok_all = True
            for fn in filenames:
                if not self.scan_media(fn):
                    ok_all = False
            return ok_all

    def ensure_adb_alive(self, aggressive=False, shell_probe=True, publish_unrecoverable=True):
        """
        ADB接続を確認し、切断されていた場合は再接続を試みる

        v1.7.5 第 1 次 改訂:
          - 旧: ADB.devices() → ADB._devices_silent() 切替 で 大量 ヘッダー 印刷 抑止 済み
        v1.7.5 第 2 次 改訂 (ADB disconnected 多発 / 30 回 全部 FAIL 悪循環 対策):
          - 旧: _devices_silent() が 空 だっ た ら 即 ADB.reconnect() (最大 30 回 2秒 毎 → 最悪 60 秒 ブロック)
          - 新: (1) まず 軽量 確認 を 2 段階 実施 、 1 つ でも OK なら 早期 return
                ① ADB.has_device() が True
                ② adb shell true を 叩き returncode == 0
               (2) 両方 ダメ だっ た 場合 だけ reconnect を 試す が max_retry=5 秒間隔 に 緩和
               (3) ADB server kill/start は 最終手段 として 例外 ブロック に 移動 (通常 シナリオ で は 発動 し ない)

        v1.7.9 aggressive モード 追加 (長時間 評価 向け):
          - aggressive=True 時 : max_retry=15 + aggressive_kill_every=5 の 強力 再接続 を 実施
          - reconnect 失敗 時 に ADB_DEVICE_UNRECOVERABLE イベント を publish し 、
            ポリシー pause_dialog なら GUI が ダイアログ を 出し て ユーザ 介入 を 促す 。

        v1.7.12 LONG 安定 化 (本質的 4 点 改訂 (ADB devices は 'device' 表示 だが GUI が 切断 と 誤判定 / その逆 を 根絶):
          - (1) shell_probe=True 時 : adb shell true の returncode==0 を 必須 条件 と し 、旧
              「has_device=True だけ で return True する 誤り を 全箇所 除去
          - (2) 第 2 次 軽量 確認 後 の 「_devices_silent が 空 で ない だけ で return True」 も 除去
              (shell_probe=True 時 は devices リスト だけ で 信用 し ない)
          - (3) reconnect() 呼び出し で 新 引数 require_shell_probe=True を 設定 し 、
              再接続 成功 判定 を 「adb devices が device かつ adb shell true rc=0」 の 二重 チェック に 強化
          - (4) ADB_DEVICE_UNRECOVERABLE publish 直前 に 1 回 限り の extra_shell_probe を 実施 し 、
              偶発的 な 競合 で reconnect 失敗 し た ケース の 誤 publish を 防止

        Ensures the ADB connection is alive and attempts reconnection if disconnected.
        v1.7.5/v1.7.9/v1.7.12 revisions: lightweight two-stage checks first, throttle-safe
        reconnect with a shell probe, and an aggressive mode for long-running scenarios.
        """
        def _publish_fail(detail=""):
            if not publish_unrecoverable:
                return
            try:
                from nas2gp.core import events as _ev_un
                try:
                    _ev_un.publish(_ev_un.ADB_DEVICE_UNRECOVERABLE,
                                   {"detail": str(detail or "")[:200],
                                    "aggressive": bool(aggressive)})
                except Exception:
                    pass
            except Exception:
                pass

        try:
            try:
                if ADB.has_device():
                    if not shell_probe:
                        return True
                    try:
                        r2 = ADB.shell_output("true", max_reconnect_retry=1)
                        if isinstance(r2, str):
                            return True
                        if hasattr(r2, "returncode") and int(getattr(r2, "returncode", -1)) == 0:
                            return True
                    except Exception:
                        pass
                    try:
                        r2b = ADB._probe_shell_true()
                        if r2b:
                            return True
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                r = ADB.shell_output("true", max_reconnect_retry=1)
                if isinstance(r, str):
                    return True
                if hasattr(r, "returncode") and int(getattr(r, "returncode", -1)) == 0:
                    return True
            except Exception:
                pass
            try:
                rb = ADB._probe_shell_true()
                if rb:
                    return True
            except Exception:
                pass
            # shell_probe=False の 場合 のみ : devices リスト だけ で も 信用 する (UI バックグラウンド 軽量 判定 向け)
            # Only when shell_probe=False: trust the devices list alone (for lightweight UI background checks)
            if not shell_probe:
                try:
                    devices = ADB._devices_silent()
                    if devices:
                        return True
                except Exception:
                    pass
            if aggressive:
                try:
                    from nas2gp.core.logger import Logger as _PxLg
                    _PxLg.warning("[ensure_adb_alive][aggressive] ADB disconnected → strong reconnect (max 15 attempts, kill-server every 5, require_shell_probe)")
                except Exception:
                    pass
                ok = ADB.reconnect(max_retry=15, sleep_step_seconds=2.0, aggressive_kill_every=5, require_shell_probe=True)
            else:
                try:
                    from nas2gp.core.logger import Logger as _PxLg
                    _PxLg.warning("[ensure_adb_alive] ADB seems disconnected → lightweight reconnect (max 5 attempts)" + (" (require_shell_probe)" if shell_probe else ""))
                except Exception:
                    pass
                ok = ADB.reconnect(max_retry=5, sleep_step_seconds=2.0, aggressive_kill_every=0, require_shell_probe=bool(shell_probe))
            try:
                if ok:
                    from nas2gp.core.logger import Logger as _PxLg2
                    _PxLg2.success("[ensure_adb_alive] reconnect OK" + (" (aggressive + shell probe)" if aggressive else ("" if not shell_probe else " (shell probe)")))
                    return True
                else:
                    from nas2gp.core.logger import Logger as _PxLg3
                    if aggressive:
                        _PxLg3.error(tr("log.device.ensure_unrec"))
                    else:
                        _PxLg3.warning("[ensure_adb_alive] lightweight reconnect gave up (skip heavy final resort to avoid 60s block)")
            except Exception:
                pass
            if aggressive:
                extra_ok = False
                try:
                    extra_ok = bool(ADB._probe_shell_true())
                except Exception:
                    extra_ok = False
                if extra_ok:
                    try:
                        from nas2gp.core.logger import Logger as _PxLg4
                        _PxLg4.success(tr("log.device.ensure_extra_ok"))
                    except Exception:
                        pass
                    return True
                try:
                    _dev_extra = bool(ADB.has_device())
                except Exception:
                    _dev_extra = False
                _publish_fail(f"aggressive reconnect failed, devices_silent={_dev_extra} final_shell_probe={extra_ok}")
            return bool(ok)
        except Exception as ex_ensure:
            try:
                from nas2gp.core.logger import Logger as _PxLg
                _PxLg.warning("[ensure_adb_alive] force ADB server restart (kill/start) as final exception fallback")
            except Exception:
                pass
            try:
                ADB.kill_server()
                time.sleep(2)
                ADB.start_server()
                time.sleep(3)
            except Exception:
                pass
            try:
                fb_ok = bool(ADB._probe_shell_true())
                if fb_ok:
                    return True
            except Exception:
                pass
            if aggressive:
                _publish_fail(f"exception fallback: {type(ex_ensure).__name__}")
            return True

    def restart_google_photos(self):
        """
        Google Photos を再起動する

        Restarts Google Photos.
        """

        ADB.shell(
            "am",
            "force-stop",
            "com.google.android.apps.photos",
        )

        time.sleep(2)

        ADB.shell(
            "monkey",
            "-p",
            "com.google.android.apps.photos",
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        )

        time.sleep(5)

    def stop_google_photos(self):
        """v1.7.4 新規: Google Photos を 停止 (force-stop) する。メモリ / プロセス を 一度 完全 に 解放。"""
        try:
            ADB.shell("am", "force-stop", "com.google.android.apps.photos")
            time.sleep(2)
            return True
        except Exception as e:
            from nas2gp.core.logger import Logger
            Logger.warning(f"stop_google_photos failed: {e}")
            return False

    def start_google_photos(self):
        """v1.7.4 新規: Google Photos を 起動 (monkey LAUNCHER) + トップ まで 確実 に スクロール まで 実施。

        v1.7.4: Launches Google Photos (monkey LAUNCHER) and reliably scrolls to the top.
        """
        try:
            ADB.shell(
                "monkey",
                "-p",
                "com.google.android.apps.photos",
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            )
            time.sleep(5)
            try:
                from nas2gp.device.ui import scroll_to_top
                scroll_to_top(force=True, label="start_google_photos")
            except Exception:
                pass
            time.sleep(3)
            return True
        except Exception as e:
            from nas2gp.core.logger import Logger
            Logger.warning(f"start_google_photos failed: {e}")
            return False

    def delete_file(self, filename):
        """
        端末内のファイルを1件削除する
        ファイル名に () スペース $ など の 特殊文字 が 含ま れ て い て も 安全 に 削除 でき る よう 、 shlex.quote で POSIX シェル クォート を 適用 する 。

        Deletes a single file on the device. Applies POSIX shell quoting via shlex.quote
        so filenames containing (), spaces, $ and other special characters are handled safely.
        """
        try:
            if not self.ensure_adb_alive(aggressive=False, shell_probe=False, publish_unrecoverable=False):
                return False
        except Exception:
            pass
        raw_remote = f"{self.WORK_DIR}/{filename}"
        quoted_remote = _sh_quote(raw_remote)
        return ADB.shell("sh", "-c", f"rm -f {quoted_remote}")

    def delete_files(self, filenames):
        """
        端末内の複数ファイルを削除する
        """
        success = 0
        try:
            self.ensure_adb_alive(aggressive=False, shell_probe=False, publish_unrecoverable=False)
        except Exception:
            pass
        for filename in filenames:
            if self.delete_file(filename):
                success += 1
        return success

    # ============================================================
    # 端末側 ファイル 削除
    #   NAS2GP 転送 ファイル 単位 の 削除 (delete_file/delete_files) に 加え 、
    #   /sdcard/DCIM 配下 を 一括 削除 する delete_dcim_all() を 用意 する 。
    #   delete_dcim_all() は 個人 の 写真 も 含め て 消える ため 、 Web UI で 二 重 確認
    #   を 経 た 場合 の み 呼ぶ こと (サーバ 側 でも 同意 + confirm を 検証 する)。
    # Device-side deletion: per-file removal of NAS2GP-transferred files
    #   (delete_file/delete_files) plus delete_dcim_all(), which wipes everything
    #   under /sdcard/DCIM. The latter also erases personal photos, so it must be
    #   called only after the double confirmation in the Web UI (the server also
    #   verifies consent + an explicit confirm flag).
    # ============================================================

    def delete_dcim_all(self):
        """v1.11.0: /sdcard/DCIM 配下 の フォルダ / ファイル を すべて 削除 する
        (DCIM フォルダ 自体 は 残す)。Google Photos の バックアップ 未 完了 の
        写真 も 消える ため 、 ユーザー の 明示 同意 が 前提 。

        v1.11.0: Deletes everything under /sdcard/DCIM (keeping the DCIM folder
        itself). This also erases photos not yet backed up to Google Photos, so it
        requires the user's explicit consent."""
        target = "/sdcard/DCIM"
        try:
            if not self.ensure_adb_alive(aggressive=True, shell_probe=True, publish_unrecoverable=False):
                return False
        except Exception:
            pass
        quoted = _sh_quote(target)
        # 中身 だけ を 消す (-mindepth 1): DCIM フォルダ 自体 は 残し 、 Google Photos の
        # 自動 バックアップ 対象 を 維持 する 。
        # Delete only the contents (-mindepth 1): keep the DCIM folder itself so it
        # remains a Google Photos auto-backup target.
        try:
            return ADB.shell(
                "sh", "-c",
                f"find {quoted} -mindepth 1 -maxdepth 1 -exec rm -rf {{}} \\;",
            )
        except Exception:
            return False

    def list_subfolders(self, base_path="/sdcard"):
        """任意 の 端末 パス 直下 の サブフォルダ 一覧 を 返す (作業フォルダ 選択 用)。
        /sdcard 配下 のみ 許可 する。戻り値: [ {"name": str, "path": str} ]

        Returns the immediate subfolders of an arbitrary device path (for choosing the working
        folder). Only paths under /sdcard are allowed. Returns [{"name","path"}].
        """
        base = str(base_path or "").strip() or "/sdcard"
        base = base.rstrip("/") or "/sdcard"
        if not base.startswith("/sdcard"):
            return []
        try:
            if not ADB.has_device():
                return []
        except Exception:
            return []
        quoted = _sh_quote(base)
        found = []
        try:
            out = ADB.shell_output(
                "sh", "-c",
                f"find {quoted} -mindepth 1 -maxdepth 1 -type d 2>/dev/null",
                max_reconnect_retry=1,
            ) or ""
            for ln in str(out).splitlines():
                p = (ln or "").strip().rstrip("/")
                if not p:
                    continue
                name = p.split("/")[-1]
                if not name or name in (".", ".."):
                    continue
                found.append({"name": name, "path": p})
        except Exception:
            return []
        found.sort(key=lambda d: d["name"].lower())
        return found

    def is_dir(self, remote_path):
        """端末 上 の remote_path が ディレクトリ か 判定 する (/sdcard 配下 のみ)。

        Returns True if remote_path on the device is a directory (only under /sdcard).
        """
        safe = str(remote_path or "").strip().rstrip("/")
        if not safe or not safe.startswith("/sdcard"):
            return False
        try:
            out = ADB.shell_output(
                "sh", "-c",
                f"[ -d {_sh_quote(safe)} ] && echo DIR || echo NO",
                max_reconnect_retry=1,
            ) or ""
            return "DIR" in str(out).upper().split("NO")[0]
        except Exception:
            return False

    def create_dir(self, remote_path):
        """端末 上 に フォルダ を 作成 する (mkdir -p, /sdcard 配下 のみ)。
        既存 なら 何も せず True を 返す 。

        Creates a folder on the device (mkdir -p, only under /sdcard); returns True if it exists.
        """
        safe = str(remote_path or "").strip().rstrip("/")
        if not safe or not safe.startswith("/sdcard"):
            return False
        try:
            if not ADB.has_device():
                return False
        except Exception:
            return False
        try:
            ADB.shell("sh", "-c", f"mkdir -p {_sh_quote(safe)}")
        except Exception:
            return False
        return self.is_dir(safe)

    def get_storage_root_free_gb(self):
        """簡易: /sdcard 配下 の 空き容量 を GB 単位 で 返す (長時間 テスト 向け 表示 用)
        端末/Android 11+ の df 出力 仕様 変更 を 考慮 し 複数 の フォールバック を 持つ。"""
        try:
            if not ADB.has_device():
                return 0.0
        except Exception:
            return 0.0
        # 1) まず POSIX 準拠 の df -Pk /sdcard を 試す (新 Android 推奨)
        # 1) First try POSIX-compliant df -Pk /sdcard (recommended on newer Android)
        try:
            out = ADB.shell_output("df", "-Pk", "/sdcard", max_reconnect_retry=1) or ""
            lines = [ln for ln in str(out).splitlines() if ln.strip()]
            if len(lines) >= 2:
                parts = lines[1].split()
                # -Pk: Filesystem 1024-blocks Used Available Capacity Mounted
                # Available は 第 4 カラム だ が、 -k と 違っ て Mounted を 除く ので 注意。
                # Available が 数値 に なる まで 右 から 探す
                # -Pk columns: Filesystem 1024-blocks Used Available Capacity Mounted.
                # Available is normally the 4th column, but Mounted is dropped unlike -k,
                # so search from the right until Available becomes numeric.
                for candidate in (3, 4):
                    try:
                        if len(parts) > candidate:
                            kb = int(parts[candidate])
                            if kb > 0:
                                return round(kb / 1024 / 1024, 2)
                    except Exception:
                        continue
        except Exception:
            pass
        # 2) 旧来 df -k /sdcard
        # 2) Legacy df -k /sdcard
        try:
            out = ADB.shell_output("df", "-k", "/sdcard", max_reconnect_retry=1) or ""
            lines = [ln for ln in str(out).splitlines() if ln.strip()]
            if len(lines) >= 2:
                parts = lines[1].split()
                if len(parts) >= 4:
                    kb = int(parts[3])
                    return round(kb / 1024 / 1024, 2)
        except Exception:
            pass
        # 3) 最終手段: 端末 クラス の free_space_gb
        # 3) Last resort: device class free_space_gb
        try:
            return float(self.free_space_gb() or 0)
        except Exception:
            return 0.0

    def run_media_scanner_and_wait(self, wait_seconds=3.0):
        """明示的 に /sdcard 全体 を MediaScanner に 再スキャン さ せ、 空き 反映 / DB 反映 を 待つ。

        Explicitly re-scans all of /sdcard with the MediaScanner and waits for free space / DB updates.
        """
        try:
            self.ensure_adb_alive(aggressive=False, shell_probe=False, publish_unrecoverable=False)
        except Exception:
            pass
        try:
            raw_uri_sd = f"file:///sdcard"
            raw_uri_dcim = f"file:///sdcard/DCIM"
            ADB.shell_output("sh", "-c",
                             f"am broadcast -a android.intent.action.MEDIA_MOUNTED -d '{raw_uri_sd}' >/dev/null 2>&1 ; "
                             f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d '{raw_uri_dcim}' >/dev/null 2>&1 ; "
                             "echo DONE",
                             max_reconnect_retry=1)
        except Exception:
            pass
        try:
            import time as _t
            _t.sleep(max(0.5, float(wait_seconds or 0)))
        except Exception:
            pass
        return True

