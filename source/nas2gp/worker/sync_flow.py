# ============================================
# FILE: nas2gp/sync.py
# ============================================

from nas2gp.core import settings as _core_settings
from nas2gp.core.settings import PHOTO_DIR, MAX_FILES_PER_BATCH, MAX_BYTES_PER_BATCH, BATCH_STRATEGY
from nas2gp.storage.db import Database
from nas2gp.uploader.flow import Uploader
from nas2gp.core.util import (
    print_phase,
    print_step,
    print_batch_header,
    print_phase_summary,
    publish_phase,
)
from nas2gp.core.i18n import tr
from nas2gp.core.util import smb_session_keep_alive


def _fmt_batch_label():
    """設定 に 応じ た バッチ ラベル を 返す (CLI / GUI ログ 表示 用)

    Return a batch label based on the settings (for CLI / GUI log display).
    """
    try:
        strategy = str(getattr(_core_settings, "BATCH_STRATEGY", BATCH_STRATEGY) or "count").strip().lower()
    except Exception:
        strategy = "count"
    if strategy == "bytes":
        try:
            mb = int(getattr(_core_settings, "MAX_BYTES_PER_BATCH", MAX_BYTES_PER_BATCH) or 0)
        except Exception:
            mb = 0
        if mb <= 0:
            return "bytes (unlimited)"
        gb = mb / (1024 ** 3)
        if gb >= 1:
            return f"{gb:.2f} GB/batch (size mode)"
        return f"{mb // (1024 ** 2)} MB/batch (size mode)"
    if strategy == "hybrid":
        try:
            cnt = int(getattr(_core_settings, "MAX_FILES_PER_BATCH", MAX_FILES_PER_BATCH) or 0)
            mb = int(getattr(_core_settings, "MAX_BYTES_PER_BATCH", MAX_BYTES_PER_BATCH) or 0)
        except Exception:
            cnt, mb = 0, 0
        if mb > 0:
            gb = mb / (1024 ** 3)
            size_part = f"{gb:.2f} GB/batch" if gb >= 1 else f"{mb // (1024 ** 2)} MB/batch"
            if cnt > 0:
                return tr("log.sync.hybrid_or").format(size=size_part, cnt=cnt)
            return tr("log.sync.hybrid_size").format(size=size_part)
        return tr("log.sync.hybrid_fallback").format(cnt=cnt or '?')
    cnt = int(getattr(_core_settings, "MAX_FILES_PER_BATCH", MAX_FILES_PER_BATCH) or 30)
    return f"{cnt} files/batch (count mode)"


def _total_batches(files_norm):
    """Uploader._build_batches と 同じ ロジック で 総バッチ数 を 算出。

    Compute the total number of batches using the same logic as Uploader._build_batches.
    """
    return len(Uploader._build_batches(files_norm, MAX_FILES_PER_BATCH, MAX_BYTES_PER_BATCH))


class Sync:

    def __init__(self, db: Database):
        self.db = db
        self.uploader = Uploader(db)

    def get_upload_list(self):
        # Uploader._normalize により str / tuple どちらも受け入れ可能だが、
        # Uploader._normalize accepts either str or tuple, but we keep
        # 本来の (path, filename) 形式を保持して渡す (DBカラムとの一貫性)
        # the original (path, filename) form (consistent with DB columns)
        return list(self.db.get_pending_files())

    def build_upload_targets(self, files):
        return files

    def upload_all(self):

        # 同期運転中 は 端末 画面 を 減光 (常夜灯モード / 省電力 ・ 焼き付き 防止)。
        # 未接続 や 失敗 は 無視 し 、 本体 処理 を 止め ない 。
        # Dim the device screen during the run (night light mode). Failures ignored.
        try:
            from nas2gp.device.ui import apply_night_brightness
            from nas2gp.core.logger import Logger as _NBLog
            if apply_night_brightness():
                _NBLog.info(tr("log.flow.night_brightness_on").format(
                    v=int(getattr(_core_settings, "SCREEN_BRIGHTNESS_NIGHT", 0) or 0)))
        except Exception:
            pass

        try:
            smb_session_keep_alive(str(PHOTO_DIR))

            # Phase 1: Transfer
            files = self.get_upload_list()
            files = self.build_upload_targets(files)

            if not files:
                # v1.14.0: 転送 対象 が 無く て も 、 端末 に 取り残さ れ た GP 未確認 ファイル が
                #   あれ ば 復旧 (バックアップ 確認 → 削除) の ため 続行 する 。
                # v1.14.0: even with no transfer targets, continue when GP-unconfirmed
                #   leftovers remain on the device so they can be recovered.
                try:
                    _has_leftover = bool(self.db.get_unconfirmed_transferred_files() or [])
                except Exception:
                    _has_leftover = False
                if not _has_leftover:
                    # v1.14.5: 未送信 1 件 も 無い (= 全部 送信 済み) こと を ホーム 画面 で
                    #   明確 に 分かる よう に する 。 コンソール 文言 と 併せ て all_sent を
                    #   通知 し 、 バナー を 「バックアップ 完了 / 未送信 は あり ませ ん」 に
                    #   切り替える 。
                    # v1.14.5: Make "everything already sent" explicit on the home screen.
                    #   Notify all_sent along with the console line so the banner can say
                    #   "Backup complete / nothing left to send".
                    print_step(tr("sync.note.all_sent"))
                    publish_phase(phase="DONE", detail=tr("sync.note.all_sent"), all_sent=True)
                    return 0, 0, 0

            norm_files = self.uploader._normalize(files)
            total_batches = _total_batches(norm_files)

            print_phase(tr("transfer.start"), phase_no=1, total_phases=4)
            print_step(f"Total target files : {len(norm_files)}")
            print_step(f"Batch strategy     : {_fmt_batch_label()}")
            print_step(f"Total batches      : {total_batches}")
            print()

            total_success, total_skipped, total_failed = self.uploader.upload_files(files)

            print_phase_summary(
                "TRANSFER",
                success=total_success,
                skipped=total_skipped,
                failed=total_failed,
            )
            return int(total_success or 0), int(total_skipped or 0), int(total_failed or 0)

        finally:
            try:
                self.db.close()
            except Exception:
                pass
            # 終了 時 は 端末 を 元 の 状態 へ 戻す (輝度 復元 → 画面 OFF)。
            # 稼働 終了 後 も プロセス (Web UI) は 生き 続ける ため 、 端末 画面 を 点灯 した
            #   まま 放置 する と 待機 中 に 電池 を 食う (長時間 評価 で 電池 切れ に 至っ た)。
            #   正常 完了 / 未送信 0 件 / 保護 停止 (電池 低下 / 容量 不足) / ユーザー 停止 /
            #   例外 の すべて が この finally を 通る 。 次 の 稼働 で 必要 な と き は wakeup さ れる 。
            # v1.14.5: 順序 と 内容 は device/ui.py の 共通 ヘルパー に 集約 し 、 エラー 経路 や
            #   停止 操作 から も 同じ 後始末 を 呼べる よう に し た 。
            # Return the device to its original state at the end of the run (brightness
            #   restore -> screen off). The process (Web UI) keeps running after a run, so
            #   leaving the screen lit drains the battery while idle. Every exit path
            #   (completion / nothing-to-send / protection stop / user stop / exception)
            #   passes through this finally; the next run wakes the screen when needed.
            # v1.14.5: The order and content live in one shared helper in device/ui.py so
            #   the error path and the stop action can run the same cleanup.
            try:
                from nas2gp.device.ui import restore_phone_state
                restore_phone_state()
            except Exception:
                pass

    @staticmethod
    def _file_size(p):
        try:
            from pathlib import Path as _P
            return _P(p).stat().st_size
        except OSError:
            return 0

    def run(self):
        return self.upload_all()



