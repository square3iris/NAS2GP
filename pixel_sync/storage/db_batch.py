# ============================================
# FILE: pixel_sync/db_batch.py
# ============================================

class BatchMixin:

    def insert_batch_history(
        self,
        started_at,
        completed_at,
        file_count,
        total_bytes,
        elapsed_seconds,
        upload_seconds,
        backup_seconds,
        status,
    ):

        self.cursor.execute("""
        INSERT INTO batch_history
        (
            started_at,
            completed_at,
            file_count,
            total_bytes,
            elapsed_seconds,
            upload_seconds,
            backup_seconds,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            started_at,
            completed_at,
            file_count,
            total_bytes,
            elapsed_seconds,
            upload_seconds,
            backup_seconds,
            status,
        ))

    def get_average_batch_seconds(self):

        self.cursor.execute("""
        SELECT AVG(elapsed_seconds)
        FROM batch_history
        WHERE status='DONE'
        """)

        row = self.cursor.fetchone()

        if not row:
            return 0

        value = row[0]

        if value is None:
            return 0

        return float(value)
