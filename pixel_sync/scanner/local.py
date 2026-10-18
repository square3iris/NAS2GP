# ============================================
# FILE: pixel_sync/scanner.py
# VERSION: 1.2.0 (PATH_PREFIX自動修正対応)
# VERSION: 1.2.0 (auto-fix for PATH_PREFIX)
# UPDATED: 2026-08-05
# ============================================
import os
import time
from pathlib import Path

from pixel_sync.core.settings import PHOTO_DIR
from pixel_sync.core.config import SUPPORTED_EXTENSIONS
from pixel_sync.storage.media import MediaFile
from pixel_sync.storage.db import Database
from pixel_sync.core.util import smb_session_keep_alive
from pixel_sync.core.i18n import tr
from pixel_sync.core import events as _events


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

    def scan(self):

        try:

            self.start_time = time.time()

            print("=" * 60)
            print(tr("scan.title"))
            print("=" * 60)
            print(tr("scan.target_dir").format(d=PHOTO_DIR))
            print()

            smb_session_keep_alive(str(PHOTO_DIR))

            self._fix_db_path_prefix()

            self._scan_dir(Path(PHOTO_DIR))

            self.db.commit()

            elapsed = time.time() - self.start_time

            print()
            print("=" * 60)
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

    def _fix_db_path_prefix(self):

        target_root = str(Path(PHOTO_DIR)).replace("\\", "/")
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
