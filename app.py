# ============================================
# FILE: app.py
# VERSION: 1.5.0 (旧mac版UI復元 / フェーズ明確化)
# UPDATED: 2026-08-07
# ============================================
import time
import atexit
import signal
import sys

from pixel_sync.db import Database
from pixel_sync.adb import ADB
from pixel_sync.scanner import Scanner
from pixel_sync.sync import Sync
from pixel_sync.logger import Logger
from pixel_sync.ui import screen_off
from pixel_sync.util import (
    print_phase,
    print_step,
    print_status,
    print_phase_summary,
)
from pixel_sync.i18n import tr


def _print_startup_header():
    print()
    print("#" * 78)
    print(f"#  {tr('ui.app_title')}".ljust(77) + "#")
    print(f"#  {tr('ui.startup')}".ljust(77) + "#")
    print("#" * 78)
    print(f"  Started at : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()


def main():

    try:
        from pixel_sync.core.config_loader import load_and_apply_gui_config
        load_and_apply_gui_config(silent=True)
    except Exception:
        pass

    _print_startup_header()

    print_phase(tr("ui.startup"), phase_no=1, total_phases=5)
    print_step(tr("app.step.check_adb"))
    devices = ADB.devices()
    if not devices:
        Logger.warning(tr("app.warn.adb_offline"))
    print_status("ADB devices", len(devices))

    print()
    print_step(tr("app.step.pixel_dcim"))
    print("-" * 78)
    for name in ADB.list_dir("/sdcard/DCIM"):
        print(f"    {name}")

    # ---- Phase 2: SCAN ----
    print_phase(tr("scan.start"), phase_no=2, total_phases=5)
    scanner = Scanner()
    scanner.scan()

    scan_total = getattr(scanner, "total_files", 0)
    scan_new = getattr(scanner, "new_files", 0)
    scan_updated = getattr(scanner, "updated_files", 0)
    scan_skip = getattr(scanner, "skipped_files", 0)
    print_phase_summary(
        "SCAN",
        success=scan_new + scan_updated,
        skipped=scan_skip,
        failed=0,
        extra={"Total catalogued": scan_total},
    )

    # ---- Phase 3-5: Sync (Transfer / Backup / Delete / Verify) ----
    print_phase("Sync Pipeline (Transfer / Backup / Delete / Verify)",
                phase_no=3, total_phases=5)

    db = Database()
    try:
        Sync(db).run()
    finally:
        try:
            db.close()
        except Exception:
            pass

    print()
    print("#" * 78)
    print(f"#  {tr('ui.shutdown')}".ljust(77) + "#")
    print(f"#  Finished at : {time.strftime('%Y-%m-%d %H:%M:%S')}".ljust(77) + "#")
    print("#" * 78)
    print()


if __name__ == "__main__":

    # atexit に加え signal (SIGINT=Ctrl+C / SIGTERM) も画面OFFハンドラ登録:
    #   atexit は通常の終了/例外で呼ばれるが 一部のOS固有のsignal殺しだと
    #   atexit 経由しない場合があるので 2重ハンドリング
    _EXIT_SIGNAL_RAN = False

    def _exit_screen_off(_signum=None, _frame=None):
        global _EXIT_SIGNAL_RAN
        if _EXIT_SIGNAL_RAN:
            return
        _EXIT_SIGNAL_RAN = True
        try:
            screen_off()
        except Exception:
            pass
        # signal handler の default 動作である exit を実施
        if _signum is not None:
            sys.exit(1)

    try:
        signal.signal(signal.SIGINT, _exit_screen_off)
        signal.signal(signal.SIGTERM, _exit_screen_off)
    except (ValueError, OSError, AttributeError):
        # Windows では SIGTERM がサポート外の場合もあるので失敗は無視
        pass

    try:

        main()

    except KeyboardInterrupt:

        print()
        Logger.warning("Interrupted by user")
        Logger.info("Safe shutdown")
        # Ctrl+C 時 明示的に screen_off を呼ぶ (signal handler で atexit だと稀に失敗する場合の保険)
        try:
            screen_off()
        except Exception:
            pass

    except Exception as e:

        # 例外時 必ず 画面OFF
        try:
            screen_off()
        except Exception:
            pass

        Logger.error(str(e))
        raise