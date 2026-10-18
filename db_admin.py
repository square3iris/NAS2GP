# ============================================
# FILE: db_admin.py
# VERSION: 2.0.0
# UPDATED: 2026-06-24
# ============================================

import sys

from pixel_sync.db_debug import DatabaseDebug
from pixel_sync.db_maintenance import DatabaseMaintenance
from pixel_sync.i18n import tr


def usage():

    print()
    print(tr("admin.title"))
    print()
    print("Commands")
    print("--------------------------------")
    print(tr("admin.cmd_reset_all"))
    print(tr("admin.cmd_reset_tr"))
    print(tr("admin.cmd_reset_g"))
    print(tr("admin.cmd_reset_del"))
    print(tr("admin.cmd_retry"))
    print(tr("admin.cmd_latest"))
    print(tr("admin.cmd_summary"))
    print(tr("admin.cmd_failed"))
    print()


def main():

    if len(sys.argv) < 2:
        usage()
        return

    command = sys.argv[1]

    if command == "reset-all":

        db = DatabaseMaintenance()
        db.reset_all()
        db.close()

    elif command == "reset-transfer":

        db = DatabaseMaintenance()
        db.reset_transfer()
        db.close()

    elif command == "reset-google":

        db = DatabaseMaintenance()
        db.reset_google_photos()
        db.close()

    elif command == "reset-delete":

        db = DatabaseMaintenance()
        db.reset_delete()
        db.close()

    elif command == "retry-failed":

        db = DatabaseMaintenance()
        db.retry_failed()
        db.close()

    elif command == "latest-new":

        count = int(sys.argv[2])

        db = DatabaseDebug()
        db.mark_latest_as_new(count)
        db.close()

    elif command == "summary":

        db = DatabaseDebug()
        db.show_summary()
        db.close()

    elif command == "failed":

        db = DatabaseDebug()
        db.show_failed()
        db.close()

    else:

        usage()


if __name__ == "__main__":
    main()
