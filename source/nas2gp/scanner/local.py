# ============================================
# FILE: nas2gp/scanner.py
# ============================================
import os
import time
from pathlib import Path

from nas2gp.core import settings as _settings
from nas2gp.core.config import SUPPORTED_EXTENSIONS
from nas2gp.storage.media import MediaFile
from nas2gp.storage.db import Database
from nas2gp.core.util import smb_session_keep_alive, publish_phase
from nas2gp.core.i18n import tr
from nas2gp.core import events as _events
from nas2gp.core import control as _control


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".heic",
    ".heif",
}


class Scanner:

    def __init__(self):

        self.db = Database()

        self.total_files = 0
        self.image_files = 0
        self.video_files = 0

        self.new_files = 0
        self.updated_files = 0
        self.skipped_files = 0

        self.start_time = 0
        self._scan_count = 0
        self.stopped = False

    def scan(self):

        try:

            self.start_time = time.time()

            # PHOTO_DIR は config/config_ui.json により 実行時 に settings へ 上書き さ れる ため 、
            #   import 時 の コピー で は なく settings から 毎回 読み直す 。
            # PHOTO_DIR is overridden at runtime by the config, so read it from settings
            #   each time instead of using the import-time copy.
            photo_dir = Path(getattr(_settings, "PHOTO_DIR", "") or "")

            # 未設定 / 不在 の まま 走査 する と カレント フォルダ を 巻き込む 恐れ が ある ため 、
            #   ここ で 明確 に 失敗 さ せる (NAS 未接続 の まま 「未送信 なし」 と 誤表示 する の も 防ぐ)。
            # Abort clearly when unset/missing: walking the CWD by accident or silently
            #   reporting "nothing to send" with an unreachable NAS are both worse.
            if not str(photo_dir) or str(photo_dir) in (".", "") or not photo_dir.exists():
                msg = tr("scan.err.invalid_root").format(d=photo_dir)
                print(msg)
                raise RuntimeError(msg)

            # 画面 (Web UI) の フェーズ 表示 を スキャン 中 に 切り替える 。
            # Switch the on-screen (Web UI) phase display to the scan phase.
            publish_phase(phase="SCAN", title=tr("scan.title"))

            print("=" * 60)
            print(tr("scan.title"))
            print("=" * 60)
            print(tr("scan.target_dir").format(d=photo_dir))
            print()

            smb_session_keep_alive(str(photo_dir))

            self._fix_db_path_prefix(photo_dir)

            self._scan_dir(photo_dir)

            self.db.commit()

            elapsed = time.time() - self.start_time

            print()
            print("=" * 60)
            if self.stopped:
                print(tr("scan.stopped"))
            else:
                print(tr("scan.done"))
            print("=" * 60)

            print(tr("scan.count.images").format(n=self.image_files))
            print(tr("scan.count.videos").format(n=self.video_files))
            print(tr("scan.count.total").format(n=self.total_files))
            print()

            print(tr("scan.count.new").format(n=self.new_files))
            print(tr("scan.count.updated").format(n=self.updated_files))
            print(tr("scan.count.skipped").format(n=self.skipped_files))
            print()

            print(tr("log.local.db_records").format(n=self.db.count()))
            print(tr("log.local.elapsed").format(sec=elapsed))

            if elapsed > 0:
                print(tr("log.local.speed").format(speed=self.total_files / elapsed))

        finally:

            self.db.close()

    def _fix_db_path_prefix(self, photo_dir):

        target_root = str(Path(photo_dir)).replace("\\", "/")
        target_drive = None
        if len(target_root) >= 2 and target_root[1] == ":":
            target_drive = target_root[:2]

        self.db.cursor.execute(r"""
        SELECT DISTINCT SUBSTRING(path, 1,
          CASE WHEN INSTR(path, '/')>0 THEN INSTR(path, '/')-1 ELSE LENGTH(path) END)
        FROM files
        WHERE INSTR(path, '/')>0 AND SUBSTRING(path,2,1)<>':'
        LIMIT 20
        """)
        unix_like_prefixes = list({r[0] for r in self.db.cursor.fetchall() if r[0]})

        need_fix_total = 0
        fixed_total = 0

        if unix_like_prefixes and target_drive is not None:
            for prefix in sorted(unix_like_prefixes):
                self.db.cursor.execute(
                    r"SELECT COUNT(*) FROM files WHERE path LIKE ?",
                    (prefix + "/%",),
                )
                cnt = self.db.cursor.fetchone()[0] or 0
                if cnt <= 0:
                    continue
                need_fix_total += cnt
                self.db.cursor.execute(
                    r"""
                    UPDATE files
                    SET path = ? || SUBSTRING(path, ?)
                    WHERE path LIKE ?
                    """,
                    (target_drive, len(prefix) + 1, prefix + "/%"),
                )
                fixed_total += self.db.cursor.rowcount or 0

        self.db.cursor.execute(r"""
        SELECT COUNT(*) FROM files WHERE path LIKE ?
        """, (target_root + "%",))
        under_target = self.db.cursor.fetchone()[0] or 0

        self.db.cursor.execute("SELECT COUNT(*) FROM files")
        all_rows = self.db.cursor.fetchone()[0] or 0

        orphans = all_rows - under_target - fixed_total
        if orphans < 0:
            orphans = 0

        if need_fix_total or fixed_total:
            print(
                tr("log.local.path_fix").format(need=need_fix_total, fixed=fixed_total)
            )

        if orphans:
            print(
                tr("log.local.path_warn").format(orphans=orphans)
            )

        if fixed_total:
            self.db.commit()

    def _scan_dir(self, folder: Path):

        try:

            with os.scandir(folder) as entries:

                for entry in entries:

                    # ユーザー 停止 要求 を 受けたら 走査 を 打ち切る (NAS 全体 の 走査 は
                    #   数分 かかる こと が あり 、 停止 が 効か ない と 操作 でき なく なる)。
                    # Abort the walk when the user requests a stop (a full NAS walk can take
                    #   minutes and an unresponsive stop would leave the UI unusable).
                    if self.stopped or _control.stop_now_requested():
                        self.stopped = True
                        return

                    if entry.name.startswith("."):
                        continue

                    if entry.is_dir(follow_symlinks=False):
                        self._scan_dir(Path(entry.path))
                        continue

                    ext = Path(entry.name).suffix.lower()

                    if ext not in SUPPORTED_EXTENSIONS:
                        continue

                    try:
                        self._scan_count += 1
                        if self._scan_count % 500 == 0:
                            _events.publish(
                                _events.SCAN_PROGRESS_TICK,
                                {"scanned": self._scan_count, "current_file": str(Path(entry.path))},
                            )
                    except Exception:
                        pass

                    self.total_files += 1

                    if ext in IMAGE_EXTENSIONS:
                        self.image_files += 1
                        media_type = "IMAGE"
                    else:
                        self.video_files += 1
                        media_type = "VIDEO"

                    stat = entry.stat()

                    media = MediaFile(
                        path=Path(entry.path),
                        filename=entry.name,
                        extension=ext,
                        size=stat.st_size,
                        mtime=stat.st_mtime,
                        media_type=media_type,
                    )

                    record = self.db.exists(media.path)

                    if record is None:

                        self.db.insert_file(media)
                        self.new_files += 1

                    else:

                        old_size, old_mtime = record

                        if (
                            old_size != media.size
                            or old_mtime != media.mtime
                        ):

                            self.db.update_file(media)
                            self.updated_files += 1

                        else:

                            self.skipped_files += 1

                    if self.total_files % 1000 == 0:

                        elapsed = time.time() - self.start_time

                        speed = (
                            self.total_files / elapsed
                            if elapsed > 0
                            else 0
                        )

                        print(
                            tr("log.local.progress_line").format(
                                total=self.total_files, speed=speed,
                                new=self.new_files, updated=self.updated_files,
                                skipped=self.skipped_files,
                            )
                        )

        except PermissionError:

            print(tr("log.local.permission_skip").format(folder=folder))
