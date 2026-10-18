# ============================================
# FILE: pixel_sync/pixel.py
# VERSION: 1.5.0 (ensure_adb_alive 追加)
# VERSION: 1.5.0 (added ensure_adb_alive)
# UPDATED: 2026-08-07
# ============================================

import time
import shlex
from pathlib import PurePosixPath

from pixel_sync.device.adb import ADB
from pixel_sync.core.i18n import tr
from pixel_sync.core.settings import (
    PIXEL_CAMERA_DIR,
    PIXEL_BACKUP_DIR,
    ADB_RECONNECT_INTERVAL,
)


def _sh_quote(posix_path):
    s = str(posix_path or "").strip()
    if not s:
        return "''"
    return shlex.quote(s)


class Pixel:

    CAMERA_DIR = PIXEL_CAMERA_DIR
    BACKUP_DIR = PIXEL_BACKUP_DIR

    def camera_files(self):

        return set(
            ADB.list_dir(self.CAMERA_DIR)
        )

    def upload(self, local_path, filename):

        remote = f"{self.CAMERA_DIR}/{filename}"

        return ADB.push(
            local_path,
            remote,
        )

    def reboot_device(self, wait_seconds_after_reboot=45.0):
        """
        Pixel 本体 を 再起動 (adb reboot) する 。
        ただし ADB 接続 が 切れ て いる 場合は コマンド が 届か ない ため False を 返し 、 呼び出し側 で ユーザー 介入 ダイアログ へ 委ねる 。
        成功 時 は 再起動 後 の ADB 再接続 まで wait_seconds_after_reboot 秒 待機 し 、 復旧 確認 でき た 場合 True を 返す 。
        """
        from pixel_sync.core.logger import Logger
        try:
            if not self.ensure_adb_alive(aggressive=True, shell_probe=True, publish_unrecoverable=False):
                Logger.warning(tr("log.pixel.reboot_adb_ng"))
                return False
        except Exception:
            return False
        try:
            if not ADB.has_device():
                Logger.warning(tr("log.pixel.reboot_no_device"))
                return False
        except Exception:
            return False
        try:
            Logger.info(tr("log.pixel.reboot_issue"))
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
                    Logger.info(tr("log.pixel.reboot_ok").format(elapsed=round(_rbt.time()-_rbt_t0,1)))
                    return True
                Logger.warning(tr("log.pixel.reboot_ng").format(elapsed=round(_rbt.time()-_rbt_t0,1)))
                return False
            except Exception as e:
                Logger.warning(tr("log.pixel.reboot_wait_exc").format(exc_type=type(e).__name__, exc=e))
                return False
        except Exception as e:
            Logger.error(f"[device] reboot_device FAILED: {type(e).__name__}: {e}")
            return False


    def scan_media(self, filename):
        """
        MediaScannerへ登録する (単体)
        """
        raw_path = f"{self.CAMERA_DIR}/{str(filename or '')}"
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
            from pixel_sync.core.util import print_step as _ps
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
            camera_prefix = str(self.CAMERA_DIR).rstrip("/")
            script = (
                f"{printf_part} | while IFS= read -r f; do "
                f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
                f"-d \"file://{camera_prefix}/$f\" >/dev/null 2>&1; "
                f"done"
            )
            return ADB.shell("sh", "-c", script)
        except Exception as e:
            from pixel_sync.core.logger import Logger
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
                from pixel_sync.core import events as _ev_un
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
                    from pixel_sync.core.logger import Logger as _PxLg
                    _PxLg.warning("[ensure_adb_alive][aggressive] ADB disconnected → strong reconnect (max 15 attempts, kill-server every 5, require_shell_probe)")
                except Exception:
                    pass
                ok = ADB.reconnect(max_retry=15, sleep_step_seconds=2.0, aggressive_kill_every=5, require_shell_probe=True)
            else:
                try:
                    from pixel_sync.core.logger import Logger as _PxLg
                    _PxLg.warning("[ensure_adb_alive] ADB seems disconnected → lightweight reconnect (max 5 attempts)" + (" (require_shell_probe)" if shell_probe else ""))
                except Exception:
                    pass
                ok = ADB.reconnect(max_retry=5, sleep_step_seconds=2.0, aggressive_kill_every=0, require_shell_probe=bool(shell_probe))
            try:
                if ok:
                    from pixel_sync.core.logger import Logger as _PxLg2
                    _PxLg2.success("[ensure_adb_alive] reconnect OK" + (" (aggressive + shell probe)" if aggressive else ("" if not shell_probe else " (shell probe)")))
                    return True
                else:
                    from pixel_sync.core.logger import Logger as _PxLg3
                    if aggressive:
                        _PxLg3.error(tr("log.pixel.ensure_unrec"))
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
                        from pixel_sync.core.logger import Logger as _PxLg4
                        _PxLg4.success(tr("log.pixel.ensure_extra_ok"))
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
                from pixel_sync.core.logger import Logger as _PxLg
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
            from pixel_sync.core.logger import Logger
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
                from pixel_sync.device.ui import scroll_to_top
                scroll_to_top(force=True, label="start_google_photos")
            except Exception:
                pass
            time.sleep(3)
            return True
        except Exception as e:
            from pixel_sync.core.logger import Logger
            Logger.warning(f"start_google_photos failed: {e}")
            return False

    def delete_file(self, filename):
        """
        Pixel内のファイルを1件削除する
        ファイル名に () スペース $ など の 特殊文字 が 含ま れ て い て も 安全 に 削除 でき る よう 、 shlex.quote で POSIX シェル クォート を 適用 する 。

        Deletes a single file on the Pixel. Applies POSIX shell quoting via shlex.quote
        so filenames containing (), spaces, $ and other special characters are handled safely.
        """
        try:
            if not self.ensure_adb_alive(aggressive=False, shell_probe=False, publish_unrecoverable=False):
                return False
        except Exception:
            pass
        raw_remote = f"{self.CAMERA_DIR}/{filename}"
        quoted_remote = _sh_quote(raw_remote)
        return ADB.shell("sh", "-c", f"rm -f {quoted_remote}")

    def delete_files(self, filenames):
        """
        Pixel内の複数ファイルを削除する
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
    # v1.7.5 新規: 端末 側 の 写真 フォルダ 一覧 取得 (読み取り 専用)
    # v1.8.0: 危険 な フォルダ 一括 削除 (delete_folder_recursive) は 個人 の 写真 を
    #         巻き込む 恐れ が あ る ため 撤廃 し た 。 削除 は delete_file() に よ る
    #         NAS2GP 転送 ファイル 単位 の 削除 のみ と する 。
    # v1.7.5 new: list device photo folders (read-only)
    # v1.8.0: removed the dangerous bulk folder delete (delete_folder_recursive) as it
    #         could wipe personal photos; deletion is limited to per-file removal of
    #         NAS2GP-transferred files via delete_file().
    # ============================================================

    def list_dcim_folders(self):
        """
        /sdcard/DCIM 配下 の 直下 フォルダ 一覧 を 取得 する。
        戻り値: [ {"name": str, "path": str, "count": int, "size_bytes": int} ]
        ※ count / size_bytes が 取れ なかっ た 場合 は 0 埋め で 返す。

        Gets the list of immediate subfolders under /sdcard/DCIM.
        Returns [ {"name","path","count","size_bytes"} ]; count/size_bytes default to 0 when unavailable.
        """
        base = "/sdcard/DCIM"
        try:
            if not ADB.has_device():
                return []
        except Exception:
            return []
        names = []
        try:
            raw = ADB.list_dir(base) or []
            for n in raw:
                s = (n or "").strip()
                if not s:
                    continue
                if s in (".", ".."):
                    continue
                names.append(s)
        except Exception:
            return []
        # adb shell で 各 フォルダ の 中身 件数 と サイズ を 概算
        # Estimate each folder's item count and size via adb shell
        result = []
        for name in names:
            sub_path = f"{base}/{name}"
            count = 0
            size_bytes = 0
            try:
                out = ADB.shell_output("sh", "-c",
                    f"ls -1 '{sub_path}' 2>/dev/null | wc -l ; "
                    f"du -sk '{sub_path}' 2>/dev/null | awk '{{print $1}}'",
                    max_reconnect_retry=1,
                )
                if out:
                    lines = [ln.strip() for ln in str(out).splitlines() if ln.strip()]
                    if len(lines) >= 1 and lines[0].isdigit():
                        count = int(lines[0])
                    if len(lines) >= 2 and lines[1].isdigit():
                        size_bytes = int(lines[1]) * 1024
            except Exception:
                count = 0
                size_bytes = 0
            # ディレクトリ か 判定 (file だけ なら 一覧 に 表示 しない 方 が 安全)
            # Determine whether it is a directory (safer to hide plain files)
            is_dir = True
            try:
                t = ADB.shell_output("sh", "-c",
                                     f"[ -d '{sub_path}' ] && echo DIR || echo NOT_DIR",
                                     max_reconnect_retry=1) or ""
                if "NOT_DIR" in str(t).upper():
                    is_dir = False
            except Exception:
                is_dir = True
            if is_dir:
                result.append({
                    "name": name,
                    "path": sub_path,
                    "count": int(count or 0),
                    "size_bytes": int(size_bytes or 0),
                })
        return result

    def get_storage_root_free_gb(self):
        """簡易: /sdcard 配下 の 空き容量 を GB 単位 で 返す (長時間 テスト 向け 表示 用)
        Pixel/Android 11+ の df 出力 仕様 変更 を 考慮 し 複数 の フォールバック を 持つ。"""
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
        # 3) 最終手段: Pixel クラス の free_space_gb
        # 3) Last resort: Pixel class free_space_gb
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

