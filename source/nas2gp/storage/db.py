# ============================================
# FILE: nas2gp/db.py
# ============================================

import sqlite3
from pathlib import Path

from nas2gp.core import settings as _settings
from nas2gp.storage.db_batch import BatchMixin
from nas2gp.storage.db_stats import StatsMixin


# 後方互換: db.DB_FILE を 参照 する 旧コード (db_view 等) 用 の 別名。
# Backward-compat alias for legacy code (db_view, etc.) that reads db.DB_FILE.
DB_FILE = _settings.DB_FILE


_SCHEMA_TARGET_COLUMNS = {
    "nas_path": "TEXT",
}


class Database(
    BatchMixin,
    StatsMixin,
):

    def __init__(self):

        # 実 DB パス は settings.DB_FILE を 1 箇所 の 正 と し、import 時 コピー を 使わ ない。
        # The actual DB path uses settings.DB_FILE as the single source of truth,
        # not an import-time copy.
        db_file = _settings.DB_FILE

        Path(db_file).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # v1.8.0: 旧 DB 名 (nas2gp.db) が 残っ て いれ ば 、 開く 前 に 新 DB 名 へ 移行 する 。
        # v1.8.0: if a legacy DB filename remains, migrate it to the new name before opening.
        try:
            _mig = _settings.migrate_legacy_db_file()
            if _mig:
                try:
                    from nas2gp.core.logger import Logger as _Lg
                    _Lg.info(f"[DB] legacy DB migrated: {_mig}")
                except Exception:
                    pass
        except Exception:
            pass

        self.conn = sqlite3.connect(db_file)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()

        self.create_tables()
        self._auto_migrate()

    # ============================================================
    # テーブル作成
    # Table creation
    # ============================================================

    def create_tables(self):

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS files (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            path TEXT UNIQUE,
            nas_path TEXT,
            filename TEXT,
            extension TEXT,

            size INTEGER,
            mtime REAL,

            media_type TEXT,

            nas_state TEXT,

            phone_transfer_state TEXT,

            google_photos_state TEXT,

            phone_delete_state TEXT,

            verify_state TEXT,

            transferred_at TEXT,

            backup_started_at TEXT,
            backup_completed_at TEXT,

            deleted_at TEXT,

            verified_at TEXT,

            retry_count INTEGER DEFAULT 0,

            last_error TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP

        )
        """)

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS batch_history (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            started_at TEXT,
            completed_at TEXT,

            file_count INTEGER,
            total_bytes INTEGER DEFAULT 0,

            elapsed_seconds REAL,
            upload_seconds REAL,
            backup_seconds REAL,

            status TEXT,

            created_at TEXT
                DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # v1.7.20: アプリ内部 の 小さな メタ情報 (key-value) を 保存 する 表。
        #   統計 の 「過去履歴クリア」 基準時刻 (stats_baseline) など を 保持 する。
        # v1.7.20: small key-value table for app-internal meta
        #   (e.g. the stats "clear history" baseline timestamp).
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        # v1.7.16: Web /stats ファイル一覧 の フィルタ を 高速 化 する 索引 (冪等)。
        # v1.7.16: indexes to speed up Web /stats file-list filters (idempotent).
        self.cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_transfer "
            "ON files(phone_transfer_state)"
        )
        self.cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_gp "
            "ON files(google_photos_state)"
        )
        self.cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_delete "
            "ON files(phone_delete_state)"
        )
        # v1.7.19: 統計 の 時間別 集計 (transferred_at) を 高速 化 する 索引 (冪等)。
        # v1.7.19: index to speed up the per-hour stats aggregation on transferred_at.
        self.cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_transferred_at "
            "ON files(transferred_at)"
        )

        self.conn.commit()

    # ============================================================
    # 自動スキーママイグレーション (既存DBの破壊的変更保護)
    # Automatic schema migration (guards existing DB against destructive changes)
    # ============================================================

    def _auto_migrate(self):
        from nas2gp.logger import Logger
        from nas2gp.util import backup_db_file

        try:
            existing_cols_raw = self._get_existing_columns("files")
            existing_col_names = set(name for (name, _ctype) in existing_cols_raw)
            added_any = False

            missing = [
                (cname, ctype)
                for (cname, ctype) in _SCHEMA_TARGET_COLUMNS.items()
                if cname not in existing_col_names
            ]

            if missing:
                Logger.warning(
                    f"DB schema migration required: missing columns = {missing}"
                )
                backup_path = backup_db_file(DB_FILE)
                Logger.info(f"Pre-migration backup completed: {backup_path}")

                for (cname, ctype) in missing:
                    Logger.info(f"  ADD COLUMN {cname} {ctype}")
                    self.cursor.execute(
                        f"ALTER TABLE files ADD COLUMN {cname} {ctype}"
                    )
                    added_any = True

                self.conn.commit()

            if "nas_path" in (c[0] for c in missing) or self._needs_nas_path_backfill():
                Logger.info("Back-filling nas_path column from existing path column ...")
                self.cursor.execute(
                    """
                    UPDATE files
                       SET nas_path = path
                     WHERE nas_path IS NULL
                        OR nas_path = ''
                    """
                )
                migrated = self.cursor.rowcount
                if migrated and migrated > 0:
                    Logger.info(f"Migrated rows (nas_path back-fill): {migrated}")
                self.conn.commit()
                added_any = True

            if added_any:
                Logger.info("DB schema migration completed successfully.")
        except Exception as e:
            Logger.error(f"DB schema migration FAILED: {e}")
            raise

    def _get_existing_columns(self, table_name):
        rows = self.query(f"PRAGMA table_info({table_name})")
        return [(r[1], r[2]) for r in rows]

    def _needs_nas_path_backfill(self):
        try:
            r = self.query(
                """
                SELECT COUNT(*) FROM files
                 WHERE nas_path IS NULL OR nas_path = ''
                """
            )
            return (r[0][0] if r else 0) > 0
        except sqlite3.OperationalError:
            return False

    # ============================================================
    # CRUD
    # ============================================================

    def insert_file(self, media):

        self.cursor.execute("""
        INSERT OR IGNORE INTO files
        (
            path,
            nas_path,
            filename,
            extension,
            size,
            mtime,
            media_type,

            nas_state,

            phone_transfer_state,

            google_photos_state,

            phone_delete_state,

            verify_state
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(media.path),
            str(media.path),
            media.filename,
            media.extension,
            media.size,
            media.mtime,
            media.media_type,

            "NEW",
            "PENDING",
            "PENDING",
            "PENDING",
            "PENDING"
        ))

    def exists(self, path):

        self.cursor.execute(
            """
            SELECT size, mtime
            FROM files
            WHERE path=?
            """,
            (str(path),)
        )

        return self.cursor.fetchone()

    def update_file(self, media):

        self.cursor.execute("""
        UPDATE files
        SET
            nas_path=?,
            filename=?,
            extension=?,
            size=?,
            mtime=?,
            media_type=?,
            nas_state='UPDATED',
            phone_transfer_state='PENDING',
            google_photos_state='PENDING',
            phone_delete_state='PENDING',
            verify_state='PENDING',
            updated_at=CURRENT_TIMESTAMP
        WHERE path=?
        """, (
            str(media.path),
            media.filename,
            media.extension,
            media.size,
            media.mtime,
            media.media_type,
            str(media.path)
        ))

    def update_nas_state(self, path, state):

        self.cursor.execute("""
        UPDATE files
        SET
            nas_state=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE path=?
        """, (
            state,
            str(path)
        ))

    def update_phone_transfer_state(
        self,
        path,
        state
    ):
        # v1.7.19: 転送 完了 (DONE) 時 に 転送完了時刻 transferred_at を 記録 する 。
        #   従来 この カラム は 空 の まま だっ た ため 、統計 の 時間別 集計
        #   (写真/動画 の 枚数 推移) が でき なかっ た 。時刻 は batch_history と
        #   揃え て ローカル時刻 で 保存 する (CURRENT_TIMESTAMP は UTC の ため)。
        # v1.7.19: Record transferred_at when a transfer completes (DONE). This column
        #   used to stay empty, so per-hour stats (photo/video counts) were impossible.
        #   Store local time to match batch_history (CURRENT_TIMESTAMP is UTC).

        if str(state) == "DONE":
            from datetime import datetime as _dt
            self.cursor.execute("""
            UPDATE files
            SET
                phone_transfer_state=?,
                transferred_at=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE path=?
            """, (
                state,
                _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                str(path)
            ))
        else:
            self.cursor.execute("""
            UPDATE files
            SET
                phone_transfer_state=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE path=?
            """, (
                state,
                str(path)
            ))

    def revert_phone_transfer_failed_to_pending(self, paths):
        """v1.7.18: 指定 パス 群 の うち phone_transfer_state='FAILED' の 行 だけ を
        'PENDING' へ 戻す (ADB 切断 に よる 安全 停止 時 に 、次回 実行 で 自動 再送 する ため)。

        v1.7.18: Revert only rows with phone_transfer_state='FAILED' among the given
        paths back to 'PENDING' (used on a safe stop caused by an ADB disconnect so the
        files are retried automatically on the next run).
        """
        try:
            pairs = [(str(p),) for p in (paths or []) if p]
        except Exception:
            pairs = []
        if not pairs:
            return 0
        try:
            self.cursor.executemany("""
            UPDATE files
            SET
                phone_transfer_state='PENDING',
                updated_at=CURRENT_TIMESTAMP
            WHERE path=? AND phone_transfer_state='FAILED'
            """, pairs)
            return int(getattr(self.cursor, "rowcount", 0) or 0)
        except Exception:
            return 0

    def update_google_photos_state(
        self,
        path,
        state
    ):

        self.cursor.execute("""
        UPDATE files
        SET
            google_photos_state=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE path=?
        """, (
            state,
            str(path)
        ))

    def update_phone_delete_state(
        self,
        path,
        state
    ):

        self.cursor.execute("""
        UPDATE files
        SET
            phone_delete_state=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE path=?
        """, (
            state,
            str(path)
        ))

    def update_verify_state(
        self,
        path,
        state
    ):

        self.cursor.execute("""
        UPDATE files
        SET
            verify_state=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE path=?
        """, (
            state,
            str(path)
        ))

    def get_new_files(self):

        self.cursor.execute("""
        SELECT path
        FROM files
        WHERE phone_transfer_state='PENDING'
        ORDER BY id
        """)

        return [
            row["path"]
            for row in self.cursor.fetchall()
        ]

    def get_pending_files(self):

        self.cursor.execute("""
        SELECT path, filename
        FROM files
        WHERE phone_transfer_state='PENDING'
        ORDER BY id ASC
        """)

        return [
            (row["path"], row["filename"])
            for row in self.cursor.fetchall()
        ]

    def get_phone_pending_delete_files(self, limit=None):
        """Pre-1 GC Phase 用:
        Google フォト backup 完了 済み (google_photos_state='DONE') で ある に も 関わら ず
        端末 側 か ら まだ 削除 さ れ て い ない (phone_delete_state != DONE) ファイル 一覧 を 返す。
        これら の ファイル は 安全 に 端末 から 削除 し て 空き を 作れる 対象。

        Return files already backed up to Google Photos (google_photos_state='DONE')
        but not yet deleted on the device (phone_delete_state != DONE); these can be
        safely deleted from the device to free up space.
        """
        base_sql = """
        SELECT path, filename
        FROM files
        WHERE google_photos_state = 'DONE'
          AND COALESCE(phone_delete_state, 'PENDING') IN ('PENDING', 'FAILED')
        ORDER BY id ASC
        """
        if limit and int(limit) > 0:
            base_sql += f" LIMIT {int(limit)}"
        try:
            self.cursor.execute(base_sql)
            rows = self.cursor.fetchall() or []
            return [(r["path"], r["filename"]) for r in rows]
        except Exception:
            return []

    def mark_phone_delete_done_bulk(self, paths_list):
        """Pre-1 GC 削除 成功 後 の phone_delete_state 一括 'DONE' 更新。

        Bulk-set phone_delete_state='DONE' after successful Pre-1 GC deletions.
        """
        if not paths_list:
            return 0
        updated = 0
        for p in paths_list:
            try:
                self.cursor.execute("""
                UPDATE files
                SET phone_delete_state='DONE', updated_at=CURRENT_TIMESTAMP
                WHERE path=?
                  AND COALESCE(phone_delete_state, 'PENDING') != 'DONE'
                """, (str(p),))
                if self.cursor.rowcount and self.cursor.rowcount > 0:
                    updated += 1
            except Exception:
                continue
        return updated

    def mark_all_phone_delete_pending_done(self):
        """DCIM フォルダ 全体 削除 など で 端末 上 の アップロード 済み ファイル が
        一括 消滅 し た 場合 に 、 削除 待ち (PENDING/FAILED) の 全 行 を DONE に 整合 さ せる。
        これ を 行わ ない と DB 上 に 削除 待ち 情報 が 残留 し 、 次回 Pre-1 GC が
        既に 存在 し ない ファイル を 再試行 し 続け る (空振り)。
        戻り値: 更新 し た 行数。

        When uploaded device files disappear in bulk (e.g. whole-DCIM deletion),
        reconcile all pending/failed deletions to DONE. Otherwise stale pending
        entries remain and the next Pre-1 GC keeps retrying non-existent files.
        Returns: number of updated rows.
        """
        try:
            self.cursor.execute("""
            UPDATE files
            SET phone_delete_state='DONE', updated_at=CURRENT_TIMESTAMP
            WHERE google_photos_state='DONE'
              AND COALESCE(phone_delete_state, 'PENDING') IN ('PENDING', 'FAILED')
            """)
            return int(self.cursor.rowcount or 0)
        except Exception:
            return 0

    def query(
        self,
        sql,
        params=None
    ):

        if params:
            self.cursor.execute(sql, params)
        else:
            self.cursor.execute(sql)

        return self.cursor.fetchall()

    def count(self):

        self.cursor.execute(
            "SELECT COUNT(*) FROM files"
        )

        return self.cursor.fetchone()[0]

    def commit(self):

        self.conn.commit()

    def close(self):

        try:
            self.conn.close()
        except Exception:
            pass

        try:
            from nas2gp.core.settings import LOG_VERBOSITY
            lv = str(LOG_VERBOSITY or "standard").lower()
        except Exception:
            lv = "standard"
        if lv == "full":
            try:
                from nas2gp.logger import Logger
                Logger.debug("Database closed")
            except Exception:
                pass
        # standard/minimal で は 一切 出力 し ない (Database closed ノイズ 抑制)
        # Suppress all output in standard/minimal mode (avoids "Database closed" noise)
