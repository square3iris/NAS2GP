# ============================================
# FILE: pixel_sync/adb.py
# VERSION: 1.7.7
# UPDATED: 2026-09-05
# ============================================

import os
import subprocess
import time
import threading
from pathlib import Path

from pixel_sync.core.logger import Logger
from pixel_sync.core.i18n import tr

# v1.7.7: Stale recover(kill-server+start-server) の連打防止用 グローバル レート リミット
#        短時間 の うち に 何度 も kill/start する と ADB サーバー が 不安定 に なる 上 、
#        1 回 あたり 最悪 10秒 以上 ブロック し て UI 応答 なし を 引き起こす ため 。
# v1.7.7: Global rate limit to prevent repeated stale recover (kill-server + start-server).
#        Repeated kill/start within a short time destabilizes the ADB server and can block
#        for 10s+ per call, making the UI unresponsive.
_RECOVER_LOCK = threading.Lock()
_LAST_RECOVER_TS = 0.0
_RECOVER_MIN_INTERVAL_SEC_DEFAULT = 45.0


def _get_recover_min_interval() -> float:
    try:
        from pixel_sync.core import settings as _adb_sets
        val = float(getattr(_adb_sets, "ADB_RECOVER_MIN_INTERVAL_SEC",
                            _RECOVER_MIN_INTERVAL_SEC_DEFAULT) or 0.0)
        if val < 0.0:
            return 0.0
        return val
    except Exception:
        return _RECOVER_MIN_INTERVAL_SEC_DEFAULT


def _throttled_recover_allowed() -> bool:
    """v1.7.7: recover を 実行 し て よい か グローバル 間引き 判定。
    v1.7.9: 最小間隔 を settings.ADB_RECOVER_MIN_INTERVAL_SEC から 動的 取得。

    v1.7.7: Globally decide whether a recover may run (throttling).
    v1.7.9: The minimum interval is read dynamically from settings.ADB_RECOVER_MIN_INTERVAL_SEC.
    """
    global _LAST_RECOVER_TS
    now = time.monotonic()
    min_interval = _get_recover_min_interval()
    with _RECOVER_LOCK:
        if min_interval <= 0.0:
            _LAST_RECOVER_TS = now
            return True
        if (now - _LAST_RECOVER_TS) < min_interval:
            return False
        _LAST_RECOVER_TS = now
        return True


def _run(*args, capture_output=True, text=True):
    return subprocess.run(
        list(args),
        capture_output=capture_output,
        text=text,
        encoding="utf-8",
        errors="replace",
    )


def _collapse_sh_c(args):
    """v1.7.14: `adb shell` は argv を スペース で 連結 し て 端末 側 シェル に 渡すため 、
    ("sh", "-c", SCRIPT) を そのまま 渡す と 端末 側 で `sh -c rm -f /path` の ように
    分解 さ れ 、 SCRIPT が 壊れる (例: `rm: Needs 1 argument`)。
    ここ で SCRIPT を POSIX クォート し た 上 で "sh -c <QUOTED>" の 1 引数 に 畳む 。
    これ により SCRIPT 内 の クォート / スペース / 括弧 / $ など を 保持 し た まま
    端末 側 シェル に 正しく 解釈 さ せ る 。"""
    try:
        if len(args) == 3 and str(args[0]) == "sh" and str(args[1]) == "-c":
            import shlex as _shlex
            return ("sh -c " + _shlex.quote(str(args[2])),)
    except Exception:
        pass
    return tuple(args)


def _norm_local_path(local_path):
    p = Path(local_path)
    if not p.is_absolute():
        p = p.resolve()
    return os.path.normpath(str(p))


def _reset_recover_throttle():
    """テスト用。

    For testing.
    """
    global _LAST_RECOVER_TS
    with _RECOVER_LOCK:
        _LAST_RECOVER_TS = 0.0


def _looks_like_successful_push(result, local_path=None):
    try:
        stdout = (getattr(result, "stdout", None) or "").strip()
        stderr = (getattr(result, "stderr", None) or "").strip()
        if not stdout:
            return False
        lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
        has_pushed_evidence = False
        _last_line = lines[-1] if lines else ""
        for ln in lines:
            l = ln.lower()
            if "file pushed," in l and ("mb/s" in l or ("bytes in" in l)):
                has_pushed_evidence = True
                break
            if "pushed," in l and "bytes in" in l:
                has_pushed_evidence = True
                break
        if not has_pushed_evidence:
            return False
        if not stderr:
            return False
        low_err = stderr.lower()
        has_eof_marker = ("failed to read copy response" in low_err) and ("eof" in low_err)
        allowed_markers = [
            "failed to read copy response",
            "eof",
            "adb: error: fail",
        ]
        allowed_only = True
        err_lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
        for ln in err_lines:
            low = ln.lower()
            hit_any = False
            for m in allowed_markers:
                if m in low:
                    hit_any = True
                    break
            if not hit_any:
                allowed_only = False
                break
        if has_eof_marker and allowed_only:
            return True
        if (not has_eof_marker) and allowed_only:
            if "adb: error: fail" in low_err:
                return True
        return False
    except Exception:
        return False


def _looks_like_soft_success_shell(result, args):
    return False


def _looks_like_stale_connection(stderr: str) -> bool:
    try:
        s = str(stderr or "").lower()
    except Exception:
        return False
    if not s:
        return False
    stale_markers = [
        "device offline",
        "offline",
        "device unauthorized",
        "unauthorized",
        "protocol fault",
        "no devices/emulators found",
        "no devices",
        "device still connecting",
        "still connecting",
    ]
    for m in stale_markers:
        if m in s:
            return True
    return False


def _looks_like_transport_error(stderr: str, returncode: int = 0) -> bool:
    """v1.7.12 LONG 再接続 安定 化 追加:
    adb devices では 'device' と 表示 さ れ て いて も 実際 の shell/push の
    通信 路 だけ が 一時的 に 不調 な ケース を 検知 し recover 発動 条件 を 緩和 する 。
    「 明らかに ADB トランスポート の 障害 」 と 判断 できる パターン なら
    _looks_like_stale_connection と 同じ 扱い で recover を 許可 する 。

    v1.7.12: Detect cases where adb devices shows 'device' but only the shell/push
    transport is temporarily broken, relaxing the recover trigger conditions.
    """
    try:
        s = str(stderr or "").lower()
    except Exception:
        return False
    if not s and int(returncode or 0) == 0:
        return False
    transport_markers = [
        "transport endpoint is not connected",
        "transport endpoint",
        "broken pipe",
        "connection reset",
        "connection refused",
        "connection timed out",
        "timed out",
        "protocol fault",
        "device offline",
        "no devices/emulators found",
        "closed",
        "eof",
        "cannot stat",
        "cannot readlink",
        "permission denied",
        "protocol version mismatch",
        "more than one device/emulator",
        "adb: error:",
        "adb: failed",
    ]
    for m in transport_markers:
        if m in s:
            return True
    # rc != 0 かつ stderr 空 / 不明 で も 、 直後 ADB.has_device() が False に 遷移 し た ケース は
    # 呼び出し 側 で 検知 し て recover する ので ここ では False の まま 。
    # Even with rc != 0 and empty/unknown stderr, if ADB.has_device() turns False right after,
    # the caller detects and recovers, so this stays False here.
    return False


class ADB:

    @staticmethod
    def _devices_silent():
        import re as _re2
        result = _run("adb", "devices")
        devices = []
        if not result or not getattr(result, "stdout", ""):
            return devices
        _pattern = _re2.compile(r"^\s*(\S+)\s+(device)(?:\s+|$)")
        for line in str(result.stdout).splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            m = _pattern.match(line)
            if m and m.group(2) == "device":
                try:
                    devices.append(str(m.group(1)).strip())
                except Exception:
                    pass
        return devices

    @staticmethod
    def _any_device_state_silent():
        import re as _re3
        result = _run("adb", "devices")
        if not result or not getattr(result, "stdout", ""):
            return False
        _any_pat = _re3.compile(r"^\s*(\S+)\s+(device|offline|unauthorized|authorizing)(?:\s+|$)")
        for line in str(result.stdout).splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            if _any_pat.match(line):
                return True
        return False

    @staticmethod
    def list_devices_with_state():
        """v1.7.13: Web UI の 接続デバイス 表示 用。
        adb devices の 全 行 を パース し [{"serial","state","detail"}] を 返す 。
        state は device / offline / unauthorized / authorizing など 。

        v1.7.13: For the Web UI connected-device list. Parses all adb devices lines and
        returns [{"serial","state","detail"}]; state is device/offline/unauthorized/authorizing etc.
        """
        import re as _re_ls
        out = []
        try:
            result = _run("adb", "devices")
        except Exception:
            return out
        stdout = getattr(result, "stdout", "") or ""
        _pat = _re_ls.compile(r"^\s*(\S+)\s+(\S+)(?:\s+(.*))?$")
        _known = ("device", "offline", "unauthorized", "authorizing", "bootloader",
                  "recovery", "sideload", "host", "no", "unknown")
        for line in str(stdout).splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            m = _pat.match(line)
            if not m:
                continue
            _serial = str(m.group(1)).strip()
            _state = str(m.group(2)).strip().lower()
            if _state not in _known:
                continue
            out.append({
                "serial": _serial,
                "state": _state,
                "detail": str(m.group(3) or "").strip(),
            })
        return out

    @staticmethod
    def devices():

        print("=" * 60)
        print(tr("adb.devices.header"))
        print("=" * 60)

        result = _run("adb", "devices")

        devices = []
        import re as _re_dev
        _pattern = _re_dev.compile(r"^\s*(\S+)\s+(device)(?:\s+|$)")
        for line in result.stdout.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            m = _pattern.match(line)
            if m and m.group(2) == "device":
                try:
                    serial = str(m.group(1)).strip()
                    devices.append(serial)
                    print(serial)
                except Exception:
                    pass

        print()
        count_key = "adb.devices.count_N" if len(devices) != 0 else "adb.devices.count_0"
        print(tr(count_key).format(n=len(devices)))

        return devices

    @staticmethod
    def has_device(check_offline_too=False):
        try:
            if check_offline_too:
                saw_any = ADB._any_device_state_silent()
                return saw_any
            saw_device = bool(ADB._devices_silent())
            return saw_device
        except Exception:
            return False

    @staticmethod
    def _probe_shell_true():
        """v1.7.12 LONG 安定 化 追加: adb shell true を 1 回 叩き returncode 0 か どうか を
        True/False で 返す 軽量 ヘルパー。 adb devices で は device 表示 だが shell 通信 路 が
        不通 な ケース を detect する の に 使用 。

        v1.7.12: Lightweight helper that runs "adb shell true" once and returns whether
        returncode is 0. Used to detect cases where adb devices shows device but shell is down.
        """
        try:
            r = _run("adb", "shell", "true")
            return bool(r and int(getattr(r, "returncode", -1)) == 0)
        except Exception:
            return False

    @staticmethod
    def list_dir(path):
        try:
            if not ADB.has_device():
                return []
        except Exception:
            pass

        result = _run("adb", "shell", "ls", path)

        if result.returncode != 0:
            return []

        return [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]

    @staticmethod
    def push(local_path, remote_path):
        local_norm = _norm_local_path(local_path)

        def _do_push():
            r = _run(
                "adb",
                "push",
                local_norm,
                remote_path,
            )
            if r.returncode == 0:
                return True, r, "strict"
            if _looks_like_successful_push(r, local_path=local_norm):
                return True, r, "lenient-push-eof"
            return False, r, "strict"

        def _recover_if_needed(_stderr: str, _stage: str, *, log_level: str = "warning", returncode: int = 0):
            """v1.7.7 改版:
            - グローバル recover 間引き (45秒 に 1 回 まで) を 最優先 で 判定
            - log_level 引数 に 応じ て 最初 の 1 回 だけ WARNING , 以降 は INFO/DEBUG に 自動 格下げ
            v1.7.12 LONG 安定 化:
            - 旧条件 (stale または has_any=True, has_device=False) に 加え
              _looks_like_transport_error が True (adb devices 表示はdevice だが transport 不調) も
              recover 対象 に 加え 、 見せかけ接続 に よる 個別 コマンド 失敗 放置 を 根絶
            """
            try:
                _stale = _looks_like_stale_connection(_stderr)
            except Exception:
                _stale = False
            try:
                _transport_bad = _looks_like_transport_error(_stderr, returncode=returncode)
            except Exception:
                _transport_bad = False
            try:
                _has_any = ADB.has_device(check_offline_too=True)
                _has_device_now = ADB.has_device()
            except Exception:
                _has_any = False
                _has_device_now = False
            _trigger = bool(_stale or _transport_bad or (_has_any and not _has_device_now))
            if not _trigger:
                return
            allowed = _throttled_recover_allowed()
            if not allowed:
                # 間引き 期間 中 は recover を skip 。 DEBUG レベル のみ 記録 し て UI 上部 WARNING 欄 を 汚さ ない 。
                # Skipped during the throttle window; log at DEBUG only to keep the UI top warning clean.
                try:
                    Logger.debug(
                        tr("log.adb.push_stale_throttled").format(stage=_stage, sec=_RECOVER_MIN_INTERVAL_SEC)
                    )
                except Exception:
                    pass
                return
            # ↓ recover 実行 (WARNING は この 先頭 1 回 のみ )
            # ↓ Run recovery (WARNING only for this first occurrence)
            try:
                if log_level == "warning":
                    Logger.warning(
                        tr("log.adb.push_recover_warn").format(stage=_stage)
                    )
                else:
                    Logger.info(
                        tr("log.adb.push_recover_info").format(stage=_stage)
                    )
            except Exception:
                pass
            try:
                ADB.kill_server()
            except Exception:
                pass
            try:
                time.sleep(0.2)
            except Exception:
                pass
            try:
                ADB.start_server()
            except Exception:
                pass
            try:
                time.sleep(0.3)
            except Exception:
                pass
            try:
                _run("adb", "reconnect")
            except Exception:
                pass
            try:
                time.sleep(0.1)
            except Exception:
                pass

        try:
            ok, result, mode = _do_push()
            if ok:
                if mode == "lenient-push-eof":
                    stdout = (result.stdout or "").strip()
                    tail = ""
                    try:
                        lines = [ln for ln in stdout.splitlines() if ln.strip()]
                        if lines:
                            tail = lines[-1][:120]
                    except Exception:
                        pass
                    Logger.success(
                        f"ADB push accepted as OK (lenient=EOF+file pushed) | "
                        f"src_norm={local_norm} dst={remote_path} detail={tail}"
                    )
                return True

            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            _push_rc_1 = int(getattr(result, "returncode", -1))
            # v1.7.7: first-fail: WARNING は ファイル 毎 最初 の 1 回 のみ
            # v1.7.7: first-fail: WARNING only once per file
            Logger.warning(
                f"ADB push failed (rc={_push_rc_1}) | "
                f"src_norm={local_norm} dst={remote_path}"
            )
            if stdout:
                Logger.info(f"  stdout: {stdout[:200]}")
            if stderr:
                Logger.info(f"  stderr: {stderr[:200]}")

            _recover_if_needed(stderr, "first-fail", log_level="warning", returncode=_push_rc_1)

            try:
                if not ADB.has_device():
                    try:
                        ADB.kill_server()
                    except Exception:
                        pass
                    try:
                        ADB.start_server()
                    except Exception:
                        pass
            except Exception:
                pass

            if not ADB.reconnect(max_retry=3, sleep_step_seconds=1.0, aggressive_kill_every=0, short_fuse=True):
                return False

            ok, result, mode = _do_push()
            if ok:
                if mode == "lenient-push-eof":
                    try:
                        lines = [ln for ln in (result.stdout or "").splitlines() if ln.strip()]
                        tail = lines[-1][:120] if lines else ""
                    except Exception:
                        tail = ""
                    Logger.success(
                        f"ADB push retry accepted as OK (lenient=EOF+file pushed) | dst={remote_path} detail={tail}"
                    )
                return True

            stderr2 = (result.stderr or "").strip()
            stdout2 = (result.stdout or "").strip()
            _push_rc_2 = int(getattr(result, "returncode", -1))
            # v1.7.7: 再 push 失敗 → INFO 格下げ (WARNING を 節約)
            # v1.7.7: retry push failed -> downgrade to INFO (save WARNING)
            Logger.info(
                f"ADB push inner retry not yet (rc={_push_rc_2}) | "
                f"src_norm={local_norm} dst={remote_path}"
            )
            if stdout2:
                Logger.debug(f"  stdout: {stdout2[:200]}")
            if stderr2:
                Logger.debug(f"  stderr: {stderr2[:200]}")

            _recover_if_needed(stderr2, "retry-fail", log_level="info", returncode=_push_rc_2)

            Logger.error(
                f"ADB push retry FAILED (rc={_push_rc_2}) | "
                f"src_norm={local_norm} dst={remote_path}"
            )
            if stdout2:
                Logger.error(f"  stdout: {stdout2[:300]}")
            if stderr2:
                Logger.error(f"  stderr: {stderr2[:300]}")

            return False
        except Exception as e:
            Logger.error(f"ADB.push unexpected error: {type(e).__name__}: {e}")
            return False

    @staticmethod
    def shell(*args):
        args = _collapse_sh_c(args)
        cmd_str = " ".join(map(str, args))

        def _do_shell():
            return _run(
                "adb",
                "shell",
                *map(str, args),
            )

        def _recover_if_needed_shell(_stderr: str, _stage: str, *, log_level: str = "warning", returncode: int = 0):
            """v1.7.12: 旧 _recover_if_needed と 条件 共通化。
            _looks_like_transport_error True も recover トリガー に 加える 。

            v1.7.12: Shares conditions with the old _recover_if_needed; also adds
            _looks_like_transport_error True as a recover trigger.
            """
            try:
                _stale = _looks_like_stale_connection(_stderr)
            except Exception:
                _stale = False
            try:
                _transport_bad = _looks_like_transport_error(_stderr, returncode=returncode)
            except Exception:
                _transport_bad = False
            try:
                _has_any = ADB.has_device(check_offline_too=True)
                _has_device_now = ADB.has_device()
            except Exception:
                _has_any = False
                _has_device_now = False
            _trigger = bool(_stale or _transport_bad or (_has_any and not _has_device_now))
            if not _trigger:
                return
            allowed = _throttled_recover_allowed()
            if not allowed:
                try:
                    Logger.debug(
                        tr("log.adb.shell_throttled").format(stage=_stage)
                    )
                except Exception:
                    pass
                return
            try:
                if log_level == "warning":
                    Logger.warning(
                        tr("log.adb.shell_recover_warn").format(stage=_stage)
                    )
                else:
                    Logger.info(
                        tr("log.adb.shell_recover_info").format(stage=_stage)
                    )
            except Exception:
                pass
            try:
                ADB.kill_server()
            except Exception:
                pass
            try:
                time.sleep(0.2)
            except Exception:
                pass
            try:
                ADB.start_server()
            except Exception:
                pass
            try:
                time.sleep(0.3)
            except Exception:
                pass
            try:
                _run("adb", "reconnect")
            except Exception:
                pass
            try:
                time.sleep(0.1)
            except Exception:
                pass

        try:
            result = _do_shell()

            if result.returncode == 0:
                return True
            if _looks_like_soft_success_shell(result, args):
                return True

            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            _sh_rc_1 = int(getattr(result, "returncode", -1))
            # v1.7.7: first-fail WARNING 初回のみ
            # v1.7.7: first-fail WARNING only once
            Logger.warning(f"ADB shell failed (rc={_sh_rc_1}) | cmd=[{cmd_str}]")
            if stdout:
                Logger.info(f"  stdout: {stdout[:200]}")
            if stderr:
                Logger.info(f"  stderr: {stderr[:200]}")

            _recover_if_needed_shell(stderr, "first-fail", log_level="warning", returncode=_sh_rc_1)

            try:
                if not ADB.has_device():
                    try:
                        ADB.kill_server()
                    except Exception:
                        pass
                    try:
                        ADB.start_server()
                    except Exception:
                        pass
            except Exception:
                pass

            if not ADB.reconnect(max_retry=2, sleep_step_seconds=1.0, aggressive_kill_every=0, short_fuse=True):
                return False

            result = _do_shell()

            if result.returncode == 0:
                return True
            if _looks_like_soft_success_shell(result, args):
                return True

            stderr2 = (result.stderr or "").strip()
            stdout2 = (result.stdout or "").strip()
            _sh_rc_2 = int(getattr(result, "returncode", -1))
            # v1.7.7: 再失敗 → INFO/DEBUG 格下げ
            # v1.7.7: retry failed -> downgrade to INFO/DEBUG
            Logger.info(f"ADB shell inner retry not yet (rc={_sh_rc_2}) | cmd=[{cmd_str}]")
            if stdout2:
                Logger.debug(f"  stdout: {stdout2[:200]}")
            if stderr2:
                Logger.debug(f"  stderr: {stderr2[:200]}")

            _recover_if_needed_shell(stderr2, "retry-fail", log_level="info", returncode=_sh_rc_2)

            Logger.error(f"ADB shell retry FAILED (rc={_sh_rc_2}) | cmd=[{cmd_str}]")
            if stdout2:
                Logger.error(f"  stdout: {stdout2[:300]}")
            if stderr2:
                Logger.error(f"  stderr: {stderr2[:300]}")

            return False
        except Exception as e:
            Logger.error(f"ADB.shell unexpected error: {type(e).__name__}: {e}")
            return False

    @staticmethod
    def reconnect(max_retry=None, sleep_step_seconds=None, aggressive_kill_every=None, short_fuse=False, require_shell_probe=False):
        import os as _os
        if max_retry is None:
            env_retry = _os.environ.get("PIXELSYNC_ADB_RECONNECT_MAX_RETRY", None)
            try:
                max_retry = int(env_retry) if env_retry is not None else 30
            except Exception:
                max_retry = 30
        if sleep_step_seconds is None:
            sleep_step_seconds = 2.0
            try:
                env_step = _os.environ.get("PIXELSYNC_ADB_RECONNECT_STEP_SEC", None)
                if env_step is not None:
                    try:
                        sleep_step_seconds = float(env_step)
                        if sleep_step_seconds < 0.05:
                            sleep_step_seconds = 0.05
                    except Exception:
                        sleep_step_seconds = 2.0
            except Exception:
                sleep_step_seconds = 2.0
        try:
            aggressive_kill_every = int(aggressive_kill_every) if aggressive_kill_every is not None else 0
        except Exception:
            aggressive_kill_every = 0
        if aggressive_kill_every < 0:
            aggressive_kill_every = 0
        if short_fuse:
            aggressive_kill_every = 0
            try:
                max_retry = max(1, min(int(max_retry or 3), 5))
            except Exception:
                max_retry = 3

        def _state_info():
            try:
                res = _run("adb", "devices")
            except Exception:
                return "unknown", ""
            stdout = getattr(res, "stdout", "") or ""
            for line in str(stdout).splitlines()[1:]:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    return str(parts[1]).strip(), line
            return "empty", stdout

        def _publish_offline_if_needed(kind: str, detail: str = ""):
            try:
                from pixel_sync.core import events as _ev_mod
                try:
                    _ev_mod.publish(
                        _ev_mod.ADB_DEVICE_OFFLINE_PERSISTENT
                        if kind == "offline"
                        else _ev_mod.ADB_DEVICE_UNAUTHORIZED,
                        {"kind": kind, "detail": detail, "attempt": max_retry,
                         "step_seconds": sleep_step_seconds, "short_fuse": short_fuse},
                    )
                except Exception:
                    pass
            except Exception:
                pass

        try:
            if short_fuse:
                Logger.info(tr("log.adb.reconnect_start_short").format(max=max_retry, step=sleep_step_seconds))
            else:
                _probe_note = " (probe_shell)" if require_shell_probe else ""
                Logger.warning(tr("log.adb.reconnect_start").format(max=max_retry, step=sleep_step_seconds, note=_probe_note))
        except Exception:
            pass

        last_published_state = ""
        last_publish_ts = 0.0
        _NOTIFY_MIN_INTERVAL_SEC = 30.0

        for attempt in range(max_retry):

            if ADB.has_device():
                _shell_ok_now = True
                if require_shell_probe:
                    try:
                        _shell_ok_now = bool(ADB._probe_shell_true())
                    except Exception:
                        _shell_ok_now = False
                if _shell_ok_now:
                    try:
                        from pixel_sync.core import events as _ev_mod2
                        try:
                            _ev_mod2.publish(_ev_mod2.ADB_DEVICE_RECOVERED,
                                             {"attempt": attempt + 1, "short_fuse": short_fuse,
                                              "require_shell_probe": bool(require_shell_probe)})
                        except Exception:
                            pass
                    except Exception:
                        pass
                    if require_shell_probe:
                        Logger.success("ADB reconnected (has_device + shell probe OK)")
                    else:
                        Logger.success("ADB reconnected")
                    return True
                else:
                    try:
                        Logger.info(tr("log.adb.reconnect_shell_ng").format(n=attempt+1, max=max_retry))
                    except Exception:
                        pass

            try:
                if (attempt + 1) == 1 or (attempt + 1) == max_retry:
                    Logger.info(f"ADB disconnected retry={attempt + 1}/{max_retry}")
                else:
                    Logger.debug(f"ADB disconnected retry={attempt + 1}/{max_retry}")
            except Exception:
                pass

            now = time.monotonic()
            _state, _raw = _state_info()
            _need_publish = False
            if _state in ("unauthorized", "offline") and _state != last_published_state:
                _need_publish = True
                last_published_state = _state
            if (now - last_publish_ts) >= _NOTIFY_MIN_INTERVAL_SEC and _state in ("unauthorized", "offline", "empty"):
                _need_publish = True
                last_publish_ts = now
                last_published_state = _state
            if _need_publish:
                _publish_offline_if_needed(
                    "unauthorized" if _state == "unauthorized" else "offline",
                    detail=str(_raw)[:200],
                )

            if aggressive_kill_every > 0 and ((attempt + 1) % aggressive_kill_every == 0):
                try:
                    Logger.warning(tr("log.adb.reconnect_kill_cycle").format(n=attempt+1, max=max_retry))
                except Exception:
                    pass
                _throttle_ok = _throttled_recover_allowed()
                if not _throttle_ok:
                    try:
                        Logger.debug(tr("log.adb.reconnect_throttled"))
                    except Exception:
                        pass
                else:
                    try:
                        ADB.kill_server()
                    except Exception:
                        pass
                    try:
                        time.sleep(0.3)
                    except Exception:
                        pass
                    try:
                        ADB.start_server()
                    except Exception:
                        pass
                    try:
                        time.sleep(0.6)
                    except Exception:
                        pass
                    try:
                        _run("adb", "reconnect")
                    except Exception:
                        pass
                    try:
                        time.sleep(0.2)
                    except Exception:
                        pass
            else:
                time.sleep(sleep_step_seconds)

        if short_fuse:
            try:
                Logger.info(tr("log.adb.reconnect_short_end"))
            except Exception:
                pass
            return False

        def _final_wakeup_pixel():
            try:
                for _c in range(2):
                    try:
                        _run("adb", "shell", "input", "keyevent", "KEYCODE_WAKEUP")
                        time.sleep(0.3)
                    except Exception:
                        pass
                    try:
                        _run("adb", "shell", "input", "keyevent", "KEYCODE_MENU")
                        time.sleep(0.3)
                    except Exception:
                        pass
            except Exception:
                pass

        if _throttled_recover_allowed():
            try:
                ADB.kill_server()
            except Exception:
                pass
            try:
                time.sleep(0.4)
            except Exception:
                pass
            try:
                ADB.start_server()
            except Exception:
                pass
            try:
                time.sleep(0.8)
            except Exception:
                pass
            try:
                _run("adb", "reconnect", "offline")
            except Exception:
                pass
            try:
                time.sleep(0.6)
            except Exception:
                pass
        _fin1_has = bool(ADB.has_device())
        _fin1_shell = bool(_fin1_has and (not require_shell_probe or bool(ADB._probe_shell_true())))
        if _fin1_has and _fin1_shell:
            try:
                from pixel_sync.core import events as _ev_mod3
                try:
                    _ev_mod3.publish(_ev_mod3.ADB_DEVICE_RECOVERED, {"kind": "final-aggressive"})
                except Exception:
                    pass
            except Exception:
                pass
            if require_shell_probe:
                Logger.success("ADB reconnected (final aggressive + shell probe OK)")
            else:
                Logger.success("ADB reconnected (final aggressive)")
            return True
        elif _fin1_has and (not _fin1_shell):
            try:
                Logger.info(tr("log.adb.reconnect_final_agg_shell_ng"))
            except Exception:
                pass

        try:
            Logger.warning(tr("log.adb.reconnect_final_resort"))
        except Exception:
            pass
        _final_wakeup_pixel()
        try:
            time.sleep(0.6)
        except Exception:
            pass
        if _throttled_recover_allowed():
            try:
                ADB.kill_server()
            except Exception:
                pass
            try:
                time.sleep(0.4)
            except Exception:
                pass
            try:
                ADB.start_server()
            except Exception:
                pass
            try:
                time.sleep(1.2)
            except Exception:
                pass
        _fin2_has = bool(ADB.has_device())
        _fin2_shell = bool(_fin2_has and (not require_shell_probe or bool(ADB._probe_shell_true())))
        if _fin2_has and _fin2_shell:
            try:
                from pixel_sync.core import events as _ev_mod4
                try:
                    _ev_mod4.publish(_ev_mod4.ADB_DEVICE_RECOVERED, {"kind": "final-wakeup"})
                except Exception:
                    pass
            except Exception:
                pass
            if require_shell_probe:
                Logger.success("ADB reconnected (after final device wakeup + shell probe OK)")
            else:
                Logger.success("ADB reconnected (after final device wakeup)")
            return True
        elif _fin2_has and (not _fin2_shell):
            try:
                Logger.warning(tr("log.adb.reconnect_final_unrec"))
            except Exception:
                pass

        _final_state, _final_raw = _state_info()
        try:
            _publish_offline_if_needed(
                "unauthorized" if _final_state == "unauthorized" else "offline",
                detail=str(_final_raw)[:200],
            )
        except Exception:
            pass
        Logger.error("ADB reconnect failed")
        return False

    @staticmethod
    def kill_server():
        try:
            _run("adb", "kill-server")
            return True
        except Exception as e:
            Logger.warning(f"ADB kill_server failed: {e}")
            return False

    @staticmethod
    def start_server():
        try:
            _run("adb", "start-server")
            return True
        except Exception as e:
            Logger.warning(f"ADB start_server failed: {e}")
            return False

    @staticmethod
    def shell_output(*args, max_reconnect_retry=None, reconnect_step_seconds=None):
        args = _collapse_sh_c(args)
        try:
            if not ADB.has_device():
                return ""
        except Exception:
            pass

        def _do_so():
            return _run(
                "adb",
                "shell",
                *map(str, args),
            )

        def _recover_if_needed_so(_stderr: str, _stage: str, *, returncode: int = 0):
            """v1.7.12: push/shell と recover 条件 共通化。 transport error も recover トリガー 化。

            v1.7.12: Shares recover conditions with push/shell; transport errors also trigger recover.
            """
            try:
                _stale = _looks_like_stale_connection(_stderr)
            except Exception:
                _stale = False
            try:
                _transport_bad = _looks_like_transport_error(_stderr, returncode=returncode)
            except Exception:
                _transport_bad = False
            try:
                _has_any = ADB.has_device(check_offline_too=True)
                _has_device_now = ADB.has_device()
            except Exception:
                _has_any = False
                _has_device_now = False
            _trigger = bool(_stale or _transport_bad or (_has_any and not _has_device_now))
            if not _trigger:
                return
            allowed = _throttled_recover_allowed()
            if not allowed:
                try:
                    Logger.debug(tr("log.adb.shellout_throttled").format(stage=_stage))
                except Exception:
                    pass
                return
            try:
                Logger.info(tr("log.adb.shellout_recover").format(stage=_stage))
            except Exception:
                pass
            try:
                ADB.kill_server()
            except Exception:
                pass
            try:
                time.sleep(0.2)
            except Exception:
                pass
            try:
                ADB.start_server()
            except Exception:
                pass
            try:
                time.sleep(0.3)
            except Exception:
                pass
            try:
                _run("adb", "reconnect")
            except Exception:
                pass
            try:
                time.sleep(0.1)
            except Exception:
                pass

        try:
            result = _do_so()

            if result.returncode == 0:
                return result.stdout

            stderr_first = (result.stderr or "").strip()
            _so_rc_1 = int(getattr(result, "returncode", -1))
            _recover_if_needed_so(stderr_first, "first-fail", returncode=_so_rc_1)

            try:
                if not ADB.has_device():
                    try:
                        ADB.kill_server()
                    except Exception:
                        pass
                    try:
                        ADB.start_server()
                    except Exception:
                        pass
                    return ""
            except Exception:
                pass

            if reconnect_step_seconds is not None:
                if not ADB.reconnect(max_retry=max_reconnect_retry, sleep_step_seconds=float(reconnect_step_seconds), aggressive_kill_every=0, short_fuse=True):
                    return ""
            else:
                if not ADB.reconnect(max_retry=max_reconnect_retry, aggressive_kill_every=0, short_fuse=True):
                    return ""

            result = _do_so()

            if result.returncode == 0:
                return result.stdout

            return ""
        except Exception as e:
            Logger.error(f"ADB.shell_output unexpected error: {type(e).__name__}: {e}")
            return ""


    @staticmethod
    def free_space_gb():
        try:
            if not ADB.has_device():
                return 0
        except Exception:
            pass

        output = ADB.shell_output(
            "df",
            "-k",
            "/sdcard"
        )

        lines = [
            line.strip()
            for line in output.splitlines()
            if line.strip()
        ]

        if len(lines) < 2:
            return 0

        parts = lines[1].split()

        if len(parts) < 4:
            return 0

        available_kb = int(parts[3])

        return available_kb / 1024 / 1024

    @staticmethod
    def resolve_adb_path():
        import shutil
        return shutil.which("adb") or None

    @staticmethod
    def get_device_health(serial: str | None = None) -> dict:
        """v1.7.16: Web UI ホーム 「接続デバイス」 欄 用。
        Pixel の 空き容量 / 本体温度 (バッテリー温度) / バッテリー残量 を まとめて 取得 する。
        serial 未指定 時 は 最初 の 'device' 端末 を 対象 に する。"""
        out = {
            "ok": False,
            "serial": None,
            "storage_free_gb": None,
            "storage_total_gb": None,
            "battery_temp_c": None,
            "battery_level": None,
            "battery_charging": None,
        }
        try:
            target_serial = serial
            if not target_serial:
                try:
                    for d in (ADB.list_devices_with_state() or []):
                        if str(d.get("state")) == "device":
                            target_serial = str(d.get("serial") or "") or None
                            break
                except Exception:
                    target_serial = None
            if not target_serial:
                return out
            out["serial"] = target_serial
            serial_arg = ["-s", str(target_serial)]

            def _sh(*args):
                try:
                    r = _run("adb", *serial_arg, "shell", *[str(a) for a in args])
                    if r and int(getattr(r, "returncode", -1)) == 0:
                        return (r.stdout or "")
                except Exception:
                    pass
                return ""

            import re as _re_h
            # 空き容量 (df -k /sdcard)
            # Free space (df -k /sdcard)
            try:
                lines = [l.strip() for l in (_sh("df", "-k", "/sdcard") or "").splitlines() if l.strip()]
                if len(lines) >= 2:
                    parts = lines[1].split()
                    if len(parts) >= 4:
                        out["storage_total_gb"] = round(int(parts[1]) / 1024 / 1024, 2)
                        out["storage_free_gb"] = round(int(parts[3]) / 1024 / 1024, 2)
            except Exception:
                pass
            # 本体温度 / バッテリー残量 (dumpsys battery / temperature は 0.1℃ 単位)
            # Body temperature / battery level (dumpsys battery; temperature is in 0.1 C units)
            try:
                bat = _sh("dumpsys", "battery") or ""
                m = _re_h.search(r"^\s*temperature:\s*(-?\d+)", bat, _re_h.MULTILINE)
                if m:
                    out["battery_temp_c"] = round(int(m.group(1)) / 10.0, 1)
                m2 = _re_h.search(r"^\s*level:\s*(\d+)", bat, _re_h.MULTILINE)
                if m2:
                    out["battery_level"] = int(m2.group(1))
                # 充電 状態 (v1.7.18): AC/USB/Wireless の いずれか が true 、
                #   または status が 2(充電中)/5(満充電) なら 充電 中 と 判定 。
                # Charging state (v1.7.18): true when AC/USB/Wireless is powered,
                #   or status is 2 (charging) / 5 (full).
                try:
                    _charging = bool(_re_h.search(r"^\s*(?:AC|USB|Wireless)\s+powered:\s*true", bat, _re_h.MULTILINE))
                    if not _charging:
                        _ms = _re_h.search(r"^\s*status:\s*(\d+)", bat, _re_h.MULTILINE)
                        if _ms and int(_ms.group(1)) in (2, 5):
                            _charging = True
                    out["battery_charging"] = _charging
                except Exception:
                    pass
            except Exception:
                pass
            out["ok"] = True
        except Exception:
            pass
        return out

    @staticmethod
    def get_adb_version():
        try:
            result = _run("adb", "version")
            lines = [l.strip() for l in (result.stdout or "").splitlines() if l.strip()]
            if lines:
                return lines[0]
        except Exception:
            pass
        return None

    @staticmethod
    def get_device_info(serial: str | None = None) -> dict:
        from datetime import datetime
        result = {
            "state": "disconnected",
            "serial": None,
            "model": None,
            "manufacturer": None,
            "android_version": None,
            "build": None,
            "storage_total_gb": None,
            "storage_free_gb": None,
            "resolution_w": None,
            "resolution_h": None,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "adb_version": None,
            "adb_path": None,
        }
        try:
            result["adb_version"] = ADB.get_adb_version()
            result["adb_path"] = ADB.resolve_adb_path()
        except Exception:
            pass
        try:
            import re as _re_di
            devices_raw = _run("adb", "devices")
            device_map = {}
            _di_pat = _re_di.compile(r"^\s*(\S+)\s+(device|offline|unauthorized|authorizing)(?:\s+|$)")
            for line in (devices_raw.stdout or "").splitlines()[1:]:
                line = line.strip()
                if not line:
                    continue
                m = _di_pat.match(line)
                if m:
                    s = m.group(1)
                    st = m.group(2)
                    device_map[s] = st
            if serial and serial in device_map:
                target_serial = serial
                target_state = device_map[serial]
            elif not serial and len(device_map) > 0:
                target_serial = list(device_map.keys())[0]
                target_state = device_map[target_serial]
            else:
                return result
            result["serial"] = target_serial
            if target_state == "device":
                result["state"] = "connected"
            elif target_state == "offline":
                result["state"] = "offline"
            else:
                result["state"] = "disconnected"
                return result
            serial_arg = []
            if target_serial:
                serial_arg = ["-s", target_serial]
            def _shell_text(*args):
                try:
                    r = _run("adb", *serial_arg, "shell", *[str(a) for a in args])
                    if r.returncode == 0:
                        out = (r.stdout or "").strip()
                        if out:
                            return out
                except Exception:
                    pass
                return None
            try:
                result["model"] = _shell_text("getprop", "ro.product.model")
            except Exception:
                pass
            try:
                result["manufacturer"] = _shell_text("getprop", "ro.product.manufacturer")
            except Exception:
                pass
            try:
                result["android_version"] = _shell_text("getprop", "ro.build.version.release")
            except Exception:
                pass
            try:
                result["build"] = _shell_text("getprop", "ro.build.display.id")
            except Exception:
                pass
            try:
                wm_out = _shell_text("wm", "size")
                if wm_out:
                    for line in wm_out.splitlines():
                        line = line.strip()
                        if "Physical size:" in line or line.startswith("Physical size:"):
                            size_part = line.split(":", 1)[1].strip()
                            if "x" in size_part:
                                w, h = size_part.split("x", 1)
                                try:
                                    result["resolution_w"] = int(w.strip())
                                    result["resolution_h"] = int(h.strip())
                                except Exception:
                                    pass
            except Exception:
                pass
            try:
                df_out = _shell_text("df", "-k", "/sdcard")
                if df_out:
                    lines = [l.strip() for l in df_out.splitlines() if l.strip()]
                    if len(lines) >= 2:
                        parts = lines[1].split()
                        if len(parts) >= 4:
                            try:
                                total_kb = int(parts[1])
                                avail_kb = int(parts[3])
                                result["storage_total_gb"] = total_kb / 1024 / 1024
                                result["storage_free_gb"] = avail_kb / 1024 / 1024
                            except Exception:
                                pass
            except Exception:
                pass
        except Exception:
            pass
        return result
