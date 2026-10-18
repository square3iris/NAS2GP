# ============================================
# FILE: nas2gp/db_maintenance.py
# ============================================

from nas2gp.storage.db import Database


class DatabaseMaintenance:

    def __init__(self):

        self.db = Database()

    # Reset entire pipeline
    # 全工程リセット
    # Reset the entire pipeline
    def reset_all(self):

        self.db.cursor.execute("""
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
        """)

        self.db.commit()

    # Reset transfer stage
    # 転送工程リセット
    # Reset the transfer stage
    def reset_transfer(self):

        self.db.cursor.execute("""
        UPDATE files
        SET
            phone_transfer_state='PENDING',
            updated_at=CURRENT_TIMESTAMP
        """)
        self.db.commit()

    # Reset Google Photos stage
    # Google Photos工程リセット
    # Reset the Google Photos stage
    def reset_google_photos(self):

        self.db.cursor.execute("""
        UPDATE files
        SET
            google_photos_state='PENDING',
            updated_at=CURRENT_TIMESTAMP
        """)
        self.db.commit()

    # Reset delete stage
    # 端末削除工程リセット
    # Reset the device delete stage
    def reset_delete(self):

        self.db.cursor.execute("""
        UPDATE files
        SET
            phone_delete_state='PENDING',
            updated_at=CURRENT_TIMESTAMP
        """)
        self.db.commit()

    # Retry failed items
    # FAILED再試行
    # Retry FAILED items
    def retry_failed(self):

        self.db.cursor.execute("""
        UPDATE files
        SET phone_transfer_state='PENDING'
        WHERE phone_transfer_state='FAILED'
        """)

        self.db.cursor.execute("""
        UPDATE files
        SET google_photos_state='PENDING'
        WHERE google_photos_state='FAILED'
        """)

        self.db.cursor.execute("""
        UPDATE files
        SET phone_delete_state='PENDING'
        WHERE phone_delete_state='FAILED'
        """)

        self.db.commit()

    def close(self):

        self.db.close()
