# ============================================
# FILE: nas2gp/db_stats.py
# ============================================

class StatsMixin:

    def get_remaining_count(self):

        self.cursor.execute("""
        SELECT COUNT(*)
        FROM files
        WHERE phone_transfer_state!='DONE'
           OR phone_transfer_state IS NULL
        """)

        return self.cursor.fetchone()[0]

    def get_completed_count(self):

        self.cursor.execute("""
        SELECT COUNT(*)
        FROM files
        WHERE phone_transfer_state='DONE'
        """)

        return self.cursor.fetchone()[0]

    def get_remaining_bytes(self):

        self.cursor.execute("""
        SELECT COALESCE(SUM(size),0)
        FROM files
        WHERE phone_transfer_state!='DONE'
           OR phone_transfer_state IS NULL
        """)

        return self.cursor.fetchone()[0]

    def get_completed_bytes(self):

        self.cursor.execute("""
        SELECT COALESCE(SUM(size),0)
        FROM files
        WHERE phone_transfer_state='DONE'
        """)

        return self.cursor.fetchone()[0]

    def get_average_bytes_per_second(self):
        self.cursor.execute("""
        SELECT
            SUM(total_bytes),
            COALESCE(SUM(CASE WHEN upload_seconds IS NOT NULL AND upload_seconds > 0
                              THEN upload_seconds
                              WHEN elapsed_seconds IS NOT NULL AND elapsed_seconds > 0
                                   AND (backup_seconds IS NULL OR backup_seconds = 0 OR elapsed_seconds < backup_seconds + 1)
                              THEN elapsed_seconds
                              ELSE 0 END), 0)
        FROM batch_history
        WHERE status='DONE'
        """)
        row = self.cursor.fetchone()
        if not row:
            return 0
        total_bytes = row[0] or 0
        total_seconds = row[1] or 0
        if total_seconds < 0.1 or total_bytes <= 0:
            return 0
        speed = total_bytes / total_seconds
        if speed < 1024:
            return 0
        return speed

    def get_average_bytes_per_second_with_backup(self):
        self.cursor.execute("""
        SELECT
            SUM(total_bytes),
            SUM(elapsed_seconds)
        FROM batch_history
        WHERE status='DONE'
        """)
        row = self.cursor.fetchone()
        if not row:
            return 0
        total_bytes = row[0] or 0
        total_seconds = row[1] or 0
        if total_seconds < 0.1 or total_bytes <= 0:
            return 0
        speed = total_bytes / total_seconds
        if speed < 1024:
            return 0
        return speed

    def get_eta_seconds(self):

        speed = self.get_average_bytes_per_second()

        if speed <= 0:
            return None

        remain = self.get_remaining_bytes()

        if remain <= 0:
            return 0

        eta = int(remain / speed)

        ONE_HUNDRED_YEARS = 365 * 100 * 86400
        if eta > ONE_HUNDRED_YEARS:
            return None

        return eta
