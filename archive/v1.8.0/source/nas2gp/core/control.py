# ============================================
# FILE: nas2gp/core/control.py
# PURPOSE: 実行中 の 同期 処理 へ 「停止 要求」 を 伝える 共通 フラグ。
# PURPOSE: Shared flag to signal a "stop request" to a running sync process.
#   Web UI / GUI から 設定 し 、 uploader/flow.py が 区切り の 良い 地点 で 参照 する。
#   Set from Web UI / GUI; uploader/flow.py checks it at safe points.
# ============================================

import threading

# 即時 停止: 現在 の バッチ で 「まだ 送信 し て い ない ファイル」 の 転送 を 中断 する。
# Immediate stop: abort transfer of files not yet sent in the current batch.
#   (送信 済み ファイル の Google Photos バックアップ 待ち / 削除 は 安全 の ため 完了 さ せる)
#   (For safety, let Google Photos backup wait/delete for already-sent files finish.)
_STOP_NOW = threading.Event()

# 今回 の バッチ で 終了: 実行 中 の バッチ を 最後 まで 完了 し て から 終了 する。
# Stop after this batch: finish the running batch, then stop.
_STOP_AFTER_BATCH = threading.Event()


def reset():
    """新規 実行 の 開始 時 に 呼び出し 、 前回 の 停止 要求 を クリア する。

    Clear any previous stop requests when starting a new run.
    """
    _STOP_NOW.clear()
    _STOP_AFTER_BATCH.clear()


def request_stop_now():
    _STOP_NOW.set()


def request_stop_after_batch():
    _STOP_AFTER_BATCH.set()


def stop_now_requested() -> bool:
    return _STOP_NOW.is_set()


def stop_after_batch_requested() -> bool:
    return _STOP_AFTER_BATCH.is_set()
