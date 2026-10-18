# ============================================
# FILE: pixel_sync/db_debug.py
# VERSION: 2.0.0
# UPDATED: 2026-06-24
# ============================================

from pixel_sync.storage.db import Database


class DatabaseDebug:

    def __init__(self):

        self.db = Database()

    # Mark latest N files as NEW
    # 最新N件を再アップロード対象化
    # Re-target the latest N records for upload
    def mark_latest_as_new(self, limit):

        self.db.cursor.execute(f"""
        UPDATE files
        SET
            nas_state='NEW',
            phone_transfer_state='PENDING',
            google_photos_state='PENDING',
            phone_delete_state='PENDING',
            verify_state='PENDING',
            retry_count=0,
            last_error=NULL,
            updated_at=CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT id
            FROM files
            ORDER BY id DESC
            LIMIT {int(limit)}
        )
        """)

        self.db.commit()

    # Pipeline summary
    # パイプライン集計
    # Aggregate and print per-stage pipeline counts
    def show_summary(self):

        columns = [
            "nas_state",
            "phone_transfer_state",
            "google_photos_state",
            "phone_delete_state",
            "verify_state",
        ]

        for column in columns:

            print()
            print("=" * 60)
            print(column)
            print("=" * 60)

            self.db.cursor.execute(f"""
            SELECT {column}, COUNT(*)
            FROM files
            GROUP BY {column}
            ORDER BY COUNT(*) DESC
            """)

            rows = self.db.cursor.fetchall()

            if not rows:

                print("NO DATA")

            for state, count in rows:

                print(f"{state:<20} {count:,}")

        self.db.cursor.execute("SELECT COUNT(*) FROM files")

        total = self.db.cursor.fetchone()[0]

        print()
        print("=" * 60)
        print("TOTAL FILES")
        print("=" * 60)
        print(f"{total:,}")

    # Failed records
    # FAILEDレコード表示
    # Show FAILED records
    def show_failed(self):

        self.db.cursor.execute("""
        SELECT
            path,
            phone_transfer_state,
            google_photos_state,
            phone_delete_state,
            last_error
        FROM files
        WHERE
            phone_transfer_state='FAILED'
            OR google_photos_state='FAILED'
            OR phone_delete_state='FAILED'
        ORDER BY id
        """)

        rows = self.db.cursor.fetchall()

        if not rows:

            print("NO FAILED RECORDS")
            return

        for row in rows:

            print("-" * 60)
            print(f"PATH : {row[0]}")
            print(f"TRANSFER : {row[1]}")
            print(f"GOOGLE   : {row[2]}")
            print(f"DELETE   : {row[3]}")
            print(f"ERROR    : {row[4]}")

    def close(self):

        self.db.close()
