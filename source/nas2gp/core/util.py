# ============================================
# FILE: nas2gp/util.py
# ============================================

import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path


# ============================================
# SMB / NAS セッション維持
# SMB / NAS session keep-alive
# ============================================

def smb_session_keep_alive(path_str: str) -> bool:
    """
    SMB/NAS のセッションを維持する。
    explorer.exe を使用しない 3段階構成:
      1. Path.exists() でパス到達確認
      2. os.scandir() で1エントリだけ走査
      3. net.exe use で当該ドライブ/パスのセッションを確認
    ウィンドウを一切表示せずにバックグラウンドで実行する。

    Keep the SMB/NAS session alive.
    3-stage approach without explorer.exe:
      1. Verify path reachability via Path.exists()
      2. Scan a single entry via os.scandir()
      3. Verify the drive/path session via net.exe use
    Runs in the background with no window shown.
    """
    if not path_str:
        return False

    try:
        p = Path(path_str)
        if not p.exists():
            return False
    except OSError:
        return False

    try:
        with os.scandir(str(p)) as it:
            next(it, None)
    except OSError:
        pass

    try:
        if sys.platform.startswith("win"):
            drive = os.path.splitdrive(str(p))[0]
            if drive:
                subprocess.run(
                    ["net.exe", "use", drive],
                    capture_output=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
    except Exception:
        pass

    return True


# ============================================
# OS 連携: ファイル マネージャ で 開く
# OS integration: open in the file manager
# ============================================

def open_in_file_manager(path) -> bool:
    """
    指定 パス を OS 標準 の ファイル マネージャ で 開く 。
    Windows: os.startfile / macOS: open / Linux: xdg-open

    Open the given path with the OS default file manager.
    Windows: os.startfile / macOS: open / Linux: xdg-open

    起動 でき ない 場合 は 例外 を 送出 する (呼び出し側 で 処理)。
    Raises on failure so the caller can report the error.
    """
    p = str(Path(path))
    if sys.platform.startswith("win"):
        os.startfile(p)  # type: ignore[attr-defined]  # Windows のみ / Windows only
    else:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


# ============================================
# ファイル情報ヘルパー
# File info helpers
# ============================================

def safe_stat_ns(path):
    """
    例外を潰して安全に os.stat を呼び出す。
    失敗時は None を返す。

    Call os.stat safely, swallowing exceptions; returns None on failure.
    """
    try:
        return os.stat(path)
    except (OSError, ValueError):
        return None


def _sizeof_fmt_pure(num, suffix="B"):
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f} {unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f} Y{suffix}"


def format_bytes(num_bytes):
    """
    バイト数を人間可読な文字列に変換する。
    例: 1536 -> "1.5 KB"

    Convert a byte count to a human-readable string, e.g. 1536 -> "1.5 KB".
    """
    if num_bytes is None:
        return "?? B"
    try:
        n = int(num_bytes)
    except (TypeError, ValueError):
        return "?? B"
    return _sizeof_fmt_pure(n)


# ============================================
# バックアップ / DB 世代管理
# Backup / DB generation management
# ============================================

def backup_db_file(db_path, max_generations=7, bak_dir=None):
    """
    DBファイルを世代管理でバックアップする。
    破壊的マイグレーション前に必ず呼び出す。

    Back up the DB file with generation management.
    Always call before a destructive migration.
    """
    from nas2gp.logger import Logger

    try:
        dbp = Path(db_path)
        if not dbp.exists():
            return None

        if bak_dir is None:
            bak_dir = dbp.parent / "backups"
        else:
            bak_dir = Path(bak_dir)

        bak_dir.mkdir(parents=True, exist_ok=True)

        ts = time.strftime("%Y%m%d_%H%M%S")
        dst = bak_dir / f"{dbp.stem}_{ts}{dbp.suffix}.bak"

        shutil.copy2(str(dbp), str(dst))
        Logger.info(f"DB backup created: {dst}")

        try:
            existing = sorted(bak_dir.glob(f"{dbp.stem}_*{dbp.suffix}.bak"))
            while len(existing) > max_generations:
                old = existing.pop(0)
                try:
                    old.unlink()
                    Logger.info(f"Old DB backup removed: {old}")
                except OSError:
                    pass
        except OSError:
            pass

        return str(dst)
    except Exception as e:
        Logger.error(f"DB backup failed: {e}")
        return None


# ============================================
# コンソール UI 共通関数（旧mac版互換）
# Common console UI functions (legacy mac version compatible)
# ============================================

_TERM_WIDTH = 78


def _pad(s, width, fill=" "):
    s = str(s)
    if len(s) >= width:
        return s[:width]
    return s + fill * (width - len(s))


def publish_phase(phase=None, detail="", title=None, phase_no=None, total_phases=None, batch_idx=None):
    """v1.7.15: 現在 の 処理 フェーズ を Web UI / GUI へ 通知 する。
    コンソール に 出す 見出し と 同じ 文言 (title) を 載せ る こと で 、
    画面 表示 と コンソール を 常 に 一致 さ せ る ため の 共有 ヘルパー。
    phase: 粗い 状態 enum (UPLOAD / WAIT_BACKUP / GC / DELETE / RECOVER 等)
    title: コンソール の フェーズ 見出し (tr() 文言)
    batch_idx: バッチ 内 工程 の 場合 に 対象 バッチ 番号 (工程 インジケータ の リセット 判定 用)

    v1.7.15: Notify the Web UI / GUI of the current processing phase.
    A shared helper that carries the same text (title) as the console heading,
    keeping the on-screen display and the console consistent.
    phase: coarse state enum (UPLOAD / WAIT_BACKUP / GC / DELETE / RECOVER, etc.)
    title: console phase heading (tr() text)
    batch_idx: target batch number for intra-batch steps (used to reset the step indicator)
    """
    try:
        from nas2gp.core import events as _events
        payload = {}
        if phase:
            payload["phase"] = str(phase)
        if detail:
            payload["detail"] = str(detail)
        if title:
            payload["phase_title"] = str(title)
        if phase_no is not None:
            payload["phase_no"] = int(phase_no)
            if total_phases is not None:
                payload["phase_total"] = int(total_phases)
        if batch_idx is not None:
            payload["batch_idx"] = int(batch_idx)
        if payload:
            _events.publish(_events.SYNC_PHASE_CHANGED, payload)
    except Exception:
        pass


def print_phase(title, phase_no=None, total_phases=None):
    """
    フェーズ見出しを表示する。
    例: ========== [PHASE 2/5] フォト転送 ==========

    Print a phase heading, e.g. ========== [PHASE 2/5] photo transfer ==========
    """
    header = ""
    if phase_no is not None:
        if total_phases is not None:
            header = f" [PHASE {phase_no}/{total_phases}] "
        else:
            header = f" [PHASE {phase_no}] "
    else:
        header = " "

    inner = header + str(title) + " "
    total = len(inner)
    pad_each = max(2, (_TERM_WIDTH - total) // 2)
    bar = "=" * pad_each
    full = f"{bar}{inner}{bar}"
    if len(full) < _TERM_WIDTH:
        full += "=" * (_TERM_WIDTH - len(full))
    print()
    print(full)
    # コンソール 見出し と 同じ 文言 を Web UI へ 通知 (コンソール 出力 自体 は 変更 しない)
    # Notify the Web UI with the same text as the console heading (console output itself is unchanged)
    publish_phase(title=title, phase_no=phase_no, total_phases=total_phases)


def print_step(step_text):
    """
    ステップ行を表示する。
    例: ▶ ファイル一覧を取得中...

    Print a step line, e.g. ▶ fetching the file list...
    """
    print(f"  \u25b6 {step_text}")


def print_status(label, value, width_label=14):
    """
    ステータス行を固定幅で表示する。
    例: Status        : backing_up

    Print a fixed-width status line, e.g. Status        : backing_up
    """
    print(f"  {_pad(label, width_label)}: {value}")


def print_result_line(name, result, detail="", width_name=28):
    """
    1ファイル/1項目ごとの成否行を表示する。
    例: IMG_0001.jpg                 [ OK ]  (3.2 MB)

    Print a per-file / per-item success line, e.g. IMG_0001.jpg  [ OK ]  (3.2 MB)
    """
    mark_ok = "[ OK ]"
    mark_skip = "[SKIP]"
    mark_fail = "[FAIL]"
    mark_info = "[INFO]"

    r_norm = str(result).strip().upper()
    if r_norm in ("OK", "SUCCESS", "TRUE", "PASS", "DONE", "COMPLETE"):
        mark = mark_ok
    elif r_norm in ("SKIP", "SKIPPED", "IGNORE"):
        mark = mark_skip
    elif r_norm in ("FAIL", "FAILED", "ERROR", "NG", "FALSE"):
        mark = mark_fail
    else:
        mark = mark_info

    line = f"  {_pad(name, width_name)} {mark}"
    if detail:
        line += f"  {detail}"
    print(line)


def print_batch_header(batch_index, total_batches, file_count, total_bytes=None):
    """
    バッチ開始ヘッダを表示する。

    Print the batch start header.
    """
    size_str = ""
    if total_bytes is not None:
        size_str = f"  ({format_bytes(total_bytes)})"
    line = (
        f"  -- Batch {batch_index}"
    )
    if total_batches:
        line += f"/{total_batches}"
    line += f"  files={file_count}{size_str}  --"
    print(line)


def print_phase_summary(title, success, skipped=0, failed=0, extra=None):
    """
    フェーズ終了サマリを表示する。

    Print the phase end summary.
    """
    total = success + skipped + failed
    print()
    print("-" * _TERM_WIDTH)
    print(tr("log.util.summary_title").format(title=title))
    print(f"    Total   : {total}")
    print(f"    Success : {success}")
    if skipped:
        print(f"    Skipped : {skipped}")
    if failed:
        print(f"    Failed  : {failed}")
    if extra:
        if isinstance(extra, dict):
            for k, v in extra.items():
                print(f"    {k}: {v}")
        else:
            print(f"    {extra}")
    print("-" * _TERM_WIDTH)
    print()
