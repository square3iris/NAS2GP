# Changelog — Summary

Last updated: 2026-10-19

Format: Keep a Changelog / Versioning: Semantic Versioning.
This file is a **summary** keeping only the highlights of each version (the long history from the
removed desktop-GUI era is compressed).

---

## 2026-08 to 2026-10 — Web UI Unification (v1.7.x / current)

- **Fully removed the desktop GUI (PySide6) and unified on the Web UI**. `entry_web.py` is the standard entry point.
- The Web UI is a **7-page layout** (Home / Console / Settings / device / Management / Stats / Diagnostics) unified under a shared `base.html`.
- **Bilingual support (Japanese / English)** applied to both the Web UI and console output. External languages can be added via `config/lang/*.json`.
- **Schema-driven settings page**, reorganized into **9 groups in pipeline order (stages 1-7)** with always-visible descriptions of "which stage" and "what changes". GB-unit editing for batch size.
- **Stats page** is now analysis-only with time-series charts (transfer volume / counts / speed / battery / temperature) and period switching (24h / 7d / 30d), drawn with self-contained SVG. The file list with independent per-column filters (transfer / GP / delete) and paging **moved to the Management page** (fast even with 140k+ rows).
- Added the **Diagnostics page** (device info / DB state / language integrity / bug report ZIP) and the **Management page** (3 OPEN + 3 RESET).
- **ADB connection stabilization**: detect "fake connections" (shown as device but not actually reachable) via a shell probe.
- **Web console logs**: capture stdout/stderr with `_WebTee` and stream via SSE; noisy lines are suppressed.
- **Home screen**: status banner (traffic-light style) / 7-step in-batch indicator / connected device and health info (free space, temperature, battery) refreshed every 30 seconds.
- **Controls**: start / stop / stop-after-this-batch. All resets use the safe OLD-rename scheme.
- Added consistency handling to sync the DB delete state to `DONE` when deleting transferred files.
- Suppressed uvicorn access logs on startup; `Ctrl+C` stops promptly with `timeout_graceful_shutdown=3`.

### v1.7.18 / v1.7.19 — long-run stability / stats charts and settings reorganization

- **Long-run safety gate (v1.7.18)**: check the battery before transfer; if below the threshold and not charging, wait -> safe stop when the cap is exceeded (`BATTERY_LOW_THRESHOLD_PCT` / `BATTERY_WAIT_MAX_SEC`). On ADB disconnect, keep attempting to reconnect for a while; if unrecoverable, revert failed items to `PENDING` and stop safely (`ADB_RECOVERY_WAIT_SEC`). To cut overhead in normal runs, the connection check runs only when a transfer fails (on-demand).
- **Disconnect analysis log (v1.7.18)**: during sync, record battery / temperature / free space into `logs/health.jsonl` every 10 minutes (heartbeat). On an unrecoverable disconnect, save a snapshot to `logs/disconnect_*.log`, so you can later tell battery depletion from a sudden disconnect.
- **Stats page is now analysis-only with time-series charts (v1.7.19)**: 5 self-contained SVG charts with period switching (24h / 7d / 30d): transfer volume (GB/h), counts (photos/videos), effective speed (MB/s), battery, temperature. New API `/api/stats/timeseries`.
- **Transfer completion timestamp (v1.7.19)**: record `files.transferred_at` when a transfer is DONE (for hourly count aggregation); an index was added.
- **File list moved (v1.7.19)**: moved from the Stats page to the **Management page** (per-column filter / search / sort).
- **Settings page reorganized (v1.7.19)**: restructured into 9 groups in pipeline order, with always-visible descriptions of "which stage" and "what changes" for each group/item. Confusing internal jargon (legacy behavior / accept rule B, etc.) was replaced with plain wording.

### v1.10.0 — Usage consent gate

- **Usage consent gate (v1.10.0)**: on first launch a full-screen overlay asks you to accept the disclaimer, and the "Accept and start" button stays disabled until you tick both checkboxes. The acceptance is recorded in `source/config/consent.json` and is not shown again afterwards (no re-confirmation on version upgrades).
- **Consent module added (v1.10.0)**: added `nas2gp/core/consent.py` with `load_consent()` / `is_accepted()` / `accept(version)` / `reset()`. The record lives in `config/consent.json` (same `config/` base as config_loader), and `reset()` deletes only the single `consent.json` file.
- **Consent APIs added (v1.10.0)**: `GET /api/consent` (state) / `POST /api/consent` (record acceptance, logs one line) / `DELETE /api/consent` (clear the record, logs one line).
- **Server-side guard (v1.10.0)**: `POST /api/start` (start sync) and `POST /api/device/delete_uploaded` (delete transferred files) check consent first and return HTTP 409 with `{"ok": false, "error": "consent_required", "message": "..."}` when not accepted.
- **Settings page "Usage consent" card (v1.10.0)**: shows the consent state (accepted / not accepted) and the accepted time, with a red "Reset consent" button that clears the record (the overlay reappears on the next page load). Notices are shown inline.
- **i18n keys added (v1.10.0)**: `web.consent.*` keys added to `lang/ja.py` and `lang/en.py`.
- **Version bump (v1.10.0)**: `settings.APP_VERSION` changed from `1.9.0` to `1.10.0`. Added `**/config/consent.json` to `.gitignore` (the consent record is per-environment state, so it is not committed).

### v1.11.0 — Delete-all-under-DCIM button (manual recovery)

- **Delete-all-under-DCIM button (v1.11.0)**: added a "🧨 Delete everything under DCIM" button on the device page. As a manual recovery path when the device runs out of space during long runs, it deletes all files/folders under `/sdcard/DCIM` (keeping the DCIM folder itself, so it stays a Google Photos auto-backup target).
- **Double confirmation (v1.11.0)**: shows a warning that photos not backed up will also be deleted plus two checkboxes; the button stays disabled until both are ticked, and a final confirmation dialog is shown on execution.
- **Server-side guard (v1.11.0)**: the new API `POST /api/device/delete_dcim_all` returns 409 when consent is missing and does not run without `confirm=true`. After a successful wipe it reconciles all pending deletions to `DONE` to avoid no-op Pre-1 GC retries (`mark_all_phone_delete_pending_done()`).
- **Device-side implementation (v1.11.0)**: added `device.delete_dcim_all()` (`find /sdcard/DCIM -mindepth 1 -maxdepth 1 -exec rm -rf {} \;`).
- **Root cause of the out-of-space error (v1.11.0)**: the shortage during long runs was not a batch-processing bug. Phase 5 (backup-complete decision) timed out by mistake, so Phase 6 (device-side delete) kept being skipped and the device never regained free space. With a single multi-GB video (`batch_size=1`) the remaining count never decreases and no ETA is available, so it was misjudged as no-progress.
- **No-progress timeout improvement (v1.11.0)**: added `MIN_BACKUP_THROUGHPUT_MBPS` (default `2.0` MB/s). `batch bytes / this value` is used as the lower bound of `BACKUP_TIMEOUT` (capped by `BACKUP_ABSOLUTE_MAX_SECONDS`), so a single large file whose remaining count does not decrease is no longer cut off. A 4 GB video now waits up to about 32 minutes (previously 10).
- **Related i18n keys (v1.11.0)**: added `settings.label.min_throughput` / `settings.tooltip.min_throughput` / `bw.log.idle_timeout_scaled` to `lang/ja.py` and `lang/en.py`.
- **Version bump (v1.11.0)**: `settings.APP_VERSION` changed from `1.10.0` to `1.11.0`.

### v1.11.1 — Proportional stall-restart relaxation and timeout display/label fixes

- **Proportional relaxation of stall-restart (v1.11.1)**: for large files the "stall-restart" threshold is now relaxed in proportion, just like the no-progress timeout (added `compute_idle_timeout()` / `compute_stall_limit()` in `backup_waiter.py`). Previously, with a single large file, "backing up" persisting caused Google Photos to be restarted at short intervals. Small batches keep the previous behavior.
- **`BACKUP_STALLED_LIMIT` semantics fixed (v1.11.1)**: default changed from `150` to `15` (it is the consecutive poll count for stall-restart). The label was corrected to "Stall-Restart Threshold (polls)" and the tooltip to explain the actual time (poll-interval dependent). The UI `min` was also unified to 0 to match the tooltip's "0 disables".
- **Timeout constants unified (v1.11.1)**: `settings.py` `BACKUP_TIMEOUT` (720 -> 600) and `BACKUP_CHECK_INTERVAL` (3 -> 8) aligned to the config defaults. At runtime the config_ui.json value overrides settings, so behavior is unchanged (the old constants were dead values).
- **Timeout-related tooltip defaults corrected (v1.11.1)**: `backup_timeout` (720 -> 600) / `backup_check_interval` (30 -> 8) / `min_accept_elapsed` (90 -> 15) / `stalled_limit` (example 3 s -> 8 s) corrected to the effective values.
- **Effective value in the Phase 5 start log (v1.11.1)**: the timeout shown at batch start now uses the effective value (equivalent to `compute_idle_timeout`) instead of the base value.
- **Language key integrity fix (v1.11.1)**: renamed `log.端末.*` (9 keys) to `log.device.*` in `lang/ja.py`, clearing the diagnostics warning "Language file integrity (code=ja / miss=9)" (the code already referenced `log.device.*`).
- **Stats page description cleaned up (v1.11.1)**: removed the description about the file list (already moved to the Management page) from `web.stats.desc`, leaving only the explanation of the charts.
- **Version bump (v1.11.1)**: `settings.APP_VERSION` changed from `1.11.0` to `1.11.1`.

### v1.11.2 — Log cleanup and a clearer UNKNOWN-detection message

- **Removed the "wait reason" line (v1.11.2)**: the recurring `待機理由: ...` line during backup waiting was dropped as noise. The always-empty acceptance-hint display mechanism and the now-unused `log.gp.wait_reason` key were removed too.
- **Improved UNKNOWN-detection message (v1.11.2)**: the `[!] UNKNOWN ...` line now also shows the wording read from the screen (e.g. "バックアップの準備中" / "フォトライブラリをスキャンしています"). When nothing is recognized it prints "could not recognize the on-screen text".
- **Expanded screen-wording dictionary (v1.11.2)**: added no-space variants to the `_classify_detailed_status` keywords (e.g. "バックアップの準備中", "スキャン中", "確認中", "アップロード中"). Previously only space-separated keywords matched, so real on-screen text could be missed and treated as unknown.
- **Related i18n keys (v1.11.2)**: added `ui.screen.*` (8 keys) to `lang/ja.py` and `lang/en.py`, and added `{screen}` to `ui.scroll.unknown_recovery1/2`. Removed the now-unused `log.gp.wait_reason`.
- **Version bump (v1.11.2)**: `settings.APP_VERSION` changed from `1.11.1` to `1.11.2`.

### v1.11.3 — Widened device-deletion scope and existence check (fixing the mismatch)

- **Widened "delete only transferred files" scope (v1.11.3)**: previously only files with `google_photos_state='DONE'` (backup to Google Photos finished) **and** not yet deleted were targeted, so transferred files whose backup was unfinished remained on the device and "deleting" did not free space. The scope is now every file with `phone_transfer_state='DONE'` (transferred to the device by NAS2GP) that is not yet deleted, regardless of Google Photos state; the originals remain on the PC / NAS. Added `Database.get_phone_transferred_files()`.
- **Preview API and warning (v1.11.3)**: added `GET /api/device/delete_uploaded/preview`, returning the target count split into "Google Photos done" and "unfinished". The Web UI shows these counts in the confirmation dialog before deleting, and makes clear that unfinished files are removed from the device (originals remain on the PC / NAS).
- **Deletion with existence check (v1.11.3)**: `Device.delete_file()` now returns one of three results (removed / not present / failed). Because `rm -f` exits 0 even for missing files, the old code could falsely report "deleted" and reconcile the DB to `DONE` incorrectly. Existence check and deletion are now done in a single shell call, distinguished by marker strings. Added `Device.delete_files_detailed()` for bulk deletion (chunked, with existence check).
- **DB reconciliation corrected (v1.11.3)**: changed the condition of `Database.mark_all_phone_delete_pending_done()` to `phone_transfer_state='DONE'` and added `Database.get_phone_delete_summary()`. The Web UI delete result now shows the "not on device (treated as deleted)" count.
- **Related i18n keys (v1.11.3)**: added `web.device.del.not_found` / `web.device.confirm_uploaded_detail` / `web.device.uploaded_none` to `lang/ja.py` and `lang/en.py` (and updated `web.device.uploaded_note` to match the widened scope).
- **Version bump (v1.11.3)**: `settings.APP_VERSION` changed from `1.11.2` to `1.11.3`.

### v1.12.0 — Stats: device free-space chart and custom date-range period

- **Device free-space chart (v1.12.0)**: added a separate "💾 Device free space" line chart to the stats page. Data comes from `logs/health.jsonl` (the disconnect-analysis log, ~10-minute samples), aggregating `free_gb` per bucket (the minimum within a bucket = the tightest moment).
- **Custom date-range period (v1.12.0)**: added start/end date inputs and a "Show" button to the stats page, so you can view any past period (e.g. "just yesterday", "just last week"). The existing 24h / 7d / 30d presets remain. Added `start` / `end` ('YYYY-MM-DD') to the `/api/stats/timeseries` API.
- **Automatic bucket selection (v1.12.0)**: a range of up to 1 day buckets hourly, longer ranges daily (capped at 366 buckets).
- **Average line and average summary (v1.12.0)**: each chart now overlays a dashed period-average line, and a period-average summary (volume / speed / free space / battery / temperature / items) is shown above the charts, aimed at analysis.
- **Related i18n keys (v1.12.0)**: added `web.stats.range.custom` / `range.to` / `range.apply` / `chart.free` / `series.free` / `avg.label` / `avg.none` / `avg.summary` to `lang/ja.py` and `lang/en.py` (and updated `web.stats.desc` / `hint.health`).
- **Known limitation (v1.12.0)**: battery / temperature / free space are recorded only while syncing, so the charts have gaps for stopped periods (volume / speed remain in `batch_history` and cover past data).
- **Version bump (v1.12.0)**: `settings.APP_VERSION` changed from `1.11.3` to `1.12.0`.

### v1.13.0 — Screen dimming during the sync run (implementing Night Light Mode)

- **Screen dimming during sync (v1.13.0)**: implemented the "Night Light Mode" that previously had only settings/UI but no device-side effect. During a sync run the device brightness is lowered to `SCREEN_BRIGHTNESS_NIGHT` (default `0`), and the original brightness is automatically restored when the run ends (disable via `ENABLE_NIGHT_LIGHT_MODE`).
- **How the brightness is changed (v1.13.0)**: switch to `screen_brightness_mode=0` (manual) before setting `screen_brightness` (avoids being overridden by auto-brightness). The original values are saved and restored at the end / on abnormal exit (atexit).
- **No impact on ADB capture (v1.13.0)**: brightness is independent of screenshots (`screencap`), UIAutomator dumps and `input` events, so OCR-based backup detection and taps keep working at brightness `0` (only the backlight dims; the display stays ON).
- **Re-applied after reconnect (v1.13.0)**: because an ADB reconnect / device reboot can reset brightness, dimming is re-applied after each phase's ADB recovery.
- **Default value unified (v1.13.0)**: `SCREEN_BRIGHTNESS_NIGHT` defaulted inconsistently (`1` in `settings.py` vs `10` in `config_loader.py`); both are now `0`.
- **Related i18n / wording (v1.13.0)**: added `log.flow.night_brightness_on` to `lang/ja.py` / `lang/en.py`, and updated the settings descriptions (night group / enable / brightness) to match the "during sync run" behavior.
- **Docs (v1.13.0)**: updated section 3.6 and the config sample in `docs/{ja,en}/SETTINGS.md`.
- **Version bump (v1.13.0)**: `settings.APP_VERSION` changed from `1.12.0` to `1.13.0`.

### v1.13.1 — Settings page cleanup (removing unused items and consolidating the analysis log)

- **Removed unused settings (v1.13.1)**: removed 4 items that were shown on the Settings page but had no real effect: `ADB_TIMEOUT` (unused), `ADB_PUSH_RETRY` (actual retries are handled by `PUSH_INNER_RETRY`), `SCREEN_BRIGHTNESS_DEFAULT` (never referenced), and `CYCLE_GP_BEFORE_WAIT` (the GP cycle always runs, so ON/OFF had no effect). Existing keys in the config file are left untouched and harmless (only auto-filled from `config_loader` defaults).
- **Consolidated analysis settings (v1.13.1)**: because the disconnect analysis log and the stats heartbeat both write the same `logs/health.jsonl`, they are merged into a single checkbox "Record analysis log (disconnect analysis + stats data)". Turning it OFF stops recording, so the stats time series stops updating too. The recording-interval field is enabled only while the checkbox is ON (dependent display via `depends_on`).
- **Wording updated (v1.13.1)**: renamed the settings group to "Analysis log (disconnect analysis + stats data)" and noted that it also feeds the time-series graphs on the Stats page.
- **Docs (v1.13.1)**: updated the config sample and sections 3.3 / 3.5 / 3.6 / 3.7, and 6.3 / 6.6 in `docs/{ja,en}/SETTINGS.md`.
- **Version bump (v1.13.1)**: `settings.APP_VERSION` changed from `1.13.0` to `1.13.1`.

### v1.13.2 — Settings wording cleanup (unified around "during backup")

- **Wording cleanup (v1.13.2)**: renamed the "Device screen (brightness / night light)" group to "Device screen (dimming during backup)" and unified the item labels/descriptions to "Dim the screen during backup" / "Brightness during backup". Behavior is unchanged (dimmed during the sync run, restored to the original brightness when it ends). The internal name "night light mode" was removed from the UI.
- **Log wording (v1.13.2)**: `log.flow.night_brightness_on` changed to "Dimmed the device screen to {v} during backup".
- **Docs (v1.13.2)**: updated section 3.6 in `docs/{ja,en}/SETTINGS.md`.
- **Version bump (v1.13.2)**: `settings.APP_VERSION` changed from `1.13.1` to `1.13.2`.

### v1.13.3 — Root-cause fix for skipped device deletion + delete-state reconciliation tool

- **Root-cause fix for skipped deletion (v1.13.3)**: re-transferring a file that had previously reached "deleted (DONE)" left its DB delete state at `DONE`, permanently excluding it from `get_phone_transferred_files()`, so the manual "delete only transferred files", Pre-1 GC and Phase 6 all skipped it and the real file stayed on the device. Fixed by explicitly resetting `phone_delete_state='PENDING'` / `deleted_at=NULL` in the DONE branch of `update_phone_transfer_state()`.
- **Delete timestamp recorded (v1.13.3)**: `mark_phone_delete_done_bulk()` / `mark_all_phone_delete_pending_done()` now record the deletion time in `deleted_at` (previously NULL everywhere and unused). The reset helpers (`db_maintenance` / `db_debug`) were aligned to `deleted_at=NULL` as well.
- **Local-time unification (v1.13.3)**: `updated_at` mixed `CURRENT_TIMESTAMP` (UTC) with local time; unified on `datetime('now','localtime')` so it matches `transferred_at` / `batch_history`.
- **Delete-state reconciliation tool (v1.13.3)**: added "Reconcile delete state (repair DB/device mismatch)" on the device page. It detects files that exist on the device while the DB records them as `DONE` and re-queues them as `PENDING` (it does not delete any real file, only fixes the state). New API `POST /api/device/reconcile_delete` and DB methods `get_phone_delete_done_files()` / `mark_phone_delete_pending_bulk()`.
- **Version bump (v1.13.3)**: `settings.APP_VERSION` changed from `1.13.2` to `1.13.3`.

### v1.13.4 — Fix: screen brightness brightens again while waiting for the Google Photos backup

- **Keep dimming during the wait (v1.13.4)**: fixed the device screen brightening again while NAS2GP waits for the Google Photos backup. During the wait, unreadable progress triggers repeated recovery (`prepare_ui()` = `KEYCODE_WAKEUP` + Photos restart, and the ADB reconnect last resort `KEYCODE_WAKEUP`); the brightness was reset to the default at those screen-wake / app-restart moments. Countermeasures: (1) re-apply the dim at the end of `prepare_ui()`, and (2) re-apply it on every wait-loop tick (default 8 s).
- **Re-apply only while dimming is active (v1.13.4)**: the new helper `reapply_night_brightness()` acts only while a dim snapshot is held (i.e. dimmed for a sync run), so unrelated UI prep outside a run never darkens the screen by accident. Calling it repeatedly just re-sets the same value and is safe (the original values kept for restore are preserved).
- **Version bump (v1.13.4)**: `settings.APP_VERSION` changed from `1.13.3` to `1.13.4`.

### v1.13.5 — Re-transfer unfinished leftovers

- **Detect and re-queue leftovers (v1.13.5)**: added a way to bulk-return rows where any of the three stages (transfer / Google Photos backup / device delete) is not `DONE` to the transfer queue (`PENDING`). Rows already fully `PENDING` (initial state) are excluded because re-queuing them changes nothing. From the "Re-queue unfinished files" button in the Management page's "State repair" card you can check the count, the size and how many of them are already gone from the device, then run it. New APIs `GET /api/device/requeue_incomplete/preview` / `POST /api/device/requeue_incomplete`, new DB methods `get_incomplete_transfer_summary()` / `requeue_incomplete_transfers()`. Returns 409 when consent has not been given.
- **No roll-back of completed GP (v1.13.5)**: rows whose Google Photos is done are always fully `DONE`, so they are never targeted and a completed GP state is never rolled back to unfinished. Rows stuck at transfer `FAILED` are now retried as well.
- **Minimal re-sending (v1.13.5)**: files already gone from the device are sent again from the NAS, while files still on the device are not re-sent thanks to the same-name SKIP check (`UploadWorker.upload`) — only the backup-wait and delete phases are re-run, so no device space is wasted. The originals on the NAS are never deleted.
- **Safer manual deletion (v1.13.5)**: "delete only files that NAS2GP transferred" is now **limited to files whose Google Photos state is `DONE` (`google_photos_state='DONE'`)**, because deleting a not-yet-backed-up file from the device loses it from Google Photos (the original stays on the NAS but never reaches the cloud) and causes a flood of "transfer DONE / GP incomplete / deleted" leftovers. The excluded count and size are shown in the pre-delete confirmation dialog (`get_phone_delete_summary()` now also returns `excluded` / `excluded_bytes`).
- **State repair moved to the Management page (v1.13.5)**: moved "Reconcile delete state" and "Re-queue unfinished files" from the device page to the new "♻️ State repair" card on the Management page (above Data reset). The device page is now limited to real device operations (delete transferred files / delete everything under DCIM). New i18n keys `web.mgmt.repair.title` / `web.mgmt.repair.desc`.
- **Version bump (v1.13.5)**: `settings.APP_VERSION` changed from `1.13.4` to `1.13.5`.

### v1.13.6 — Device page delete changed to "delete everything inside the working folder"

- **Delete everything inside the working folder (v1.13.6)**: the device page delete button changed from "Delete only files transferred by NAS2GP (conditional)" to "🧹 Delete everything inside the working folder". No conditions: everything (files and subfolders) under the current working folder is removed, while the folder itself is kept. Files whose Google Photos backup is not yet finished are also deleted. Because this is destructive, the consent gate and the pre-delete confirmation dialog (with measured count/size) are retained. Under the old spec (transferred & GP-done only) the sync flow deletes from the device right after the GP backup finishes, so a manual target almost never remains and the message "there are no files to delete" appeared.
- **Measured preview API (v1.13.6)**: added `GET /api/device/delete_uploaded/preview`, which measures the file count and total size under the working folder on the device before deleting (`Device.measure_folder()`). DB state is not used as a condition.
- **Folder-contents delete implementation (v1.13.6)**: added `Device.delete_folder_contents()`. It removes only the direct children with `find ... -mindepth 1 -maxdepth 1 -exec rm -rf {} \;`, keeping the folder itself (allowed only under `/sdcard`). Afterwards the delete state of transferred rows is reconciled to `DONE` (`mark_all_phone_delete_pending_done()`).
- **Dynamic note and icon change (v1.13.6)**: the delete button's note now shows the current working folder name dynamically. The button icon changed from 🗑️ (which looked like a checkbox) to 🧹 (broom).
- **Related i18n keys (v1.13.6)**: `web.device.uploaded_note` / `uploaded_none` / `confirm_uploaded_detail` now support `{folder}` (`{excluded}` / `{excluded_gb}` removed). Added `web.device.uploaded_running` / `uploaded_result` / `uploaded_reconciled`. `web.device.delete_uploaded` changed to "🧹 Delete everything inside the working folder".
- **Docs (v1.13.6)**: updated the `delete_uploaded` description in `docs/{ja,en}/ARCHITECTURE.md`.
- **Version bump (v1.13.6)**: `settings.APP_VERSION` changed from `1.13.5` to `1.13.6`.

### v1.13.7 — Help tab and a support (donation) funnel

- **Help tab (v1.13.7)**: added "🆘 Help" (`/help`) to the nav. It gathers the overview / quick start / cautions & disclaimer / support policy / support development into one page (new `help.html` template).
- **Support (donation) funnel (v1.13.7)**: the Help page shows a link to PayPal.Me (`paypal.me/square3iris`) and a QR code. The QR is an embedded static SVG, so no external request or extra library is needed at runtime.
- **Home support link (v1.13.7)**: added a small "☕ Support development" link at the bottom of the Home page that leads to the donation section on the Help page.
- **GitHub side (v1.13.7)**: added `.github/FUNDING.yml` to show a "Sponsor" button on the repository, and a "Support development" section in the README.
- **i18n (v1.13.7)**: added `web.nav.help` / `web.home.thanks` / `web.home.support_link` / `web.help.*` to ja/en.
- **Docs (v1.13.7)**: added `/help` to the page list in `docs/{ja,en}/ARCHITECTURE.md`.
- **Version bump (v1.13.7)**: `settings.APP_VERSION` changed from `1.13.6` to `1.13.7`.

### v1.13.8 — README wording fixes (deletion spec and support funnel placement)

- **README wording fix (v1.13.8)**: updated the deletion descriptions in the overview and "Main features" to match the v1.13.6 spec. It now states that automatic deletion during sync works file by file, and that the device page has a manual button to clear the whole working folder (consent required) (ja/en).
- **Support section placement (v1.13.8)**: moved "開発を支援 / Support development" in the README from just before the License (bottom) to right after the overview (top), placing it near the download funnel (ja/en).
- **Version bump (v1.13.8)**: `settings.APP_VERSION` changed from `1.13.7` to `1.13.8`.

### v1.13.9 — Stronger battery monitoring and mid-phase resume

- **Closed the battery-monitoring gap (v1.13.9)**: the old safety gate passed whenever the device reported "charging", so it could not detect a weak charger that cannot keep up (level falling 59% → 0% overnight), and the device was found powered off in the morning. Now, if the level is not higher than the previous reading even while charging, it is treated as a low battery and the run waits until it recovers.
- **Safe stop at the absolute floor (v1.13.9)**: added `BATTERY_CRITICAL_PCT` (default `10`%). Below it, with no recovery from charging, the run stops safely regardless of the charging state. It can be changed from the "Battery" group on the Settings page.
- **Monitoring while waiting for the Google Photos backup (v1.13.9)**: stage 6 (backup-completion wait) had no battery check, leaving room for the device to power off during a long wait. The wait loop now checks the level and charging state at a fixed interval and stops safely if the level falls.
- **Resume from mid-phase (v1.13.9)**: when a run stops in the middle of a batch phase, the progress (batch index / phase number / already-processed files) is saved in `app_meta`. On the next start it asks "resume from where you left off?"; YES continues from the saved phase, NO discards the checkpoint and starts over from the beginning. The checkpoint is cleared automatically on a normal completion.
- **Related API / keys (v1.13.9)**: added `GET /api/resume_status` (reports whether a checkpoint exists to the UI) and a `resume` flag on `POST /api/start`. Added the i18n keys `web.home.resume.*` / `log.flow.battery_draining` / `log.flow.battery_critical` / `log.flow.resume_start` to ja/en.
- **Docs (v1.13.9)**: updated the config sample and sections 3.4 / 6.5 in `docs/{ja,en}/SETTINGS.md`.
- **Version bump (v1.13.9)**: `settings.APP_VERSION` changed from `1.13.8` to `1.13.9`.

### v1.14.0 — Leftover recovery (automatic reclaim of Google-Photos-unconfirmed files left on the device)

- **Closed a structural gap for leftovers (v1.14.0)**: when a run is interrupted (e.g. power loss) and files stay on the device as "transferred (`T=DONE`) / Google Photos unconfirmed (`G=PENDING`)", they matched neither the re-transfer list (`T=PENDING` only) nor Pre-1 GC (`G=DONE` only), so they slipped through forever and kept occupying device space. This was the main cause of the observed "starts at 18 GB free, drops below 10 GB" behaviour.
- **Leftover-recovery batch (v1.14.0)**: at the start of a run, leftovers still present on the device (`T=DONE` / `G=PENDING` / `D!=DONE`) are detected and prepended as leading batches **without re-transferring**; only media-scan -> Google Photos start -> backup-completion wait -> delete are re-run. Nothing is deleted before Google Photos confirmation, so no un-backed-up file is ever lost from the device.
- **Continue even with zero transfer targets (v1.14.0)**: when there are no new transfer targets but leftovers exist, the run continues for recovery (previously it exited immediately).
- **Avoid spurious capacity stalls (v1.14.0)**: for leftover-recovery and mid-phase-resume (phase 4+) batches, the targets already exist on the device (and are about to be deleted), so the Pre-0 required free-space demand is set to 0 to prevent a false "capacity short" stall / recovery sequence.
- **Priority (v1.14.0)**: an explicit mid-phase resume checkpoint takes priority; leftovers are handled on the next run.
- **Related implementation / keys (v1.14.0)**: added `Database.get_unconfirmed_transferred_files()` and implemented leftover-batch injection in `Uploader.upload_files()`. Added the i18n key `log.flow.leftover_start` to ja/en.
- **Temperature protection gate (v1.14.0)**: added a gate that pauses new transfers and waits for cooldown when the body temperature exceeds the limit. It **resumes automatically once the temperature drops 3 C below the limit**; if it does not cool within `BATTERY_WAIT_MAX_SEC`, the failed items revert to `PENDING` and the run stops safely. The temperature is checked at the same timing as the battery, so no extra ADB calls are made.
- **New setting `DEVICE_TEMP_LIMIT_C` (v1.14.0)**: the body-temperature limit (C, default 45.0). Configurable on the Settings page; `0` disables it. The default matches the "high temperature" indicator on the home screen.
- **Fixed the phase column of the device-state log (v1.14.0)**: the `phase` column of `logs/health.jsonl` was mostly (85 of 138 records observed) the literal `SYNC_PHASE_CHANGED`. A heading-only notification (`print_phase`) that carries no phase made the subscribers record the event name as the phase. The same cause also degraded the home-screen label to a generic text during long backup-completion waits; both are fixed.
- **Version bump (v1.14.0)**: `settings.APP_VERSION` changed from `1.13.9` to `1.14.0`.

### v1.9.0 — macOS / Linux support (best-effort)

- **macOS / Linux support (v1.9.0)**: made the previously Windows-only setup runnable on macOS / Linux (best-effort). Not verified on real macOS hardware.
- **OS branch for opening the file manager (v1.9.0)**: added `open_in_file_manager()` to `core/util.py`. Windows uses `os.startfile`, macOS uses `open`, Linux uses `xdg-open`, so the Web UI "open folder" action now also works on macOS / Linux.
- **POSIX launchers added (v1.9.0)**: added `.sh` / `.command` launchers under `source/bin/` (`start_web_app` / `start_app` / `db_admin` / `run_tests`). `.sh` is for the macOS / Linux terminal; `.command` is for double-clicking from macOS Finder and delegates to the matching `.sh`. They use `.venv/bin/python` if present, otherwise `python3`. The existing Windows `.cmd` launchers are unchanged.
- **Centralized dependency list (v1.9.0)**: added `requirements.txt` at the repository root (`fastapi` / `uvicorn[standard]` / `jinja2`, plus `pytest` for tests).
- **Line-ending policy (v1.9.0)**: added `.gitattributes` at the repository root, enforcing `*.cmd` / `*.bat` = CRLF, `*.sh` / `*.command` = LF, and `*.zip` / `*.db` = binary.
- **Known limitations (v1.9.0)**: the SMB / NAS session keep-alive helper (`core/util.py`, uses `net.exe`) is Windows-only and is skipped on macOS / Linux. macOS / Linux support is best-effort and has not been verified on real macOS hardware.

### v1.8.0 — product rename (NAS2GP), DB path change, generic device wording, safer deletion

- **Product rename (v1.8.0)**: renamed the product to **NAS2GP** (display name, repository name, backup name). The product name no longer embeds a specific brand trademark.
- **DB filename change (v1.8.0)**: `database/pixel_sync.db` -> `database/nas2gp.db`. On first launch the legacy DB is detected and renamed automatically (including WAL / SHM), carrying the transfer history over.
- **Generic device wording (v1.8.0)**: replaced "Pixel" in UI labels and console logs with the generic device term "端末" (Japanese UI) / "device" (English UI), so the app is not tied to a specific model.
- **Full internal rename (v1.8.0)**: unified the package name `pixel_sync` -> `nas2gp`, module `pixel.py` -> `device.py`, template `pixel.html` -> `device.html`, class `Pixel` -> `Device`, setting keys `PIXEL_*` -> `DEVICE_*` and environment variables `PIXELSYNC_*` -> `NAS2GP_*`. Legacy config keys are no longer read, but missing keys are auto-filled with defaults, so an existing `config/config_ui.json` needs no manual edit.
- **Safer deletion (v1.8.0)**: removed the "delete all of /sdcard/DCIM" operation, which risked wiping personal photos. Deletion is now limited to per-file removal of NAS2GP-transferred files (one `rm -f` at a time), and low-space recovery was changed to 2 stages: "delete transferred files -> reboot". The management-screen manual delete was also replaced with "delete transferred files only".
- **Genericized Web URLs (v1.8.0)**: the device page URL moved from `/pixel` to `/device`, and related APIs from `/api/pixel/*` to `/api/device/*`, so no specific brand name appears in the browser address bar.
- **Version shown in the Web UI + single source of truth (v1.8.0)**: the software version is now shown at the top-right of the header on every page (the header's strategy / batch-target line was also switched to white text for readability). The product version is centralized in `APP_VERSION` in `settings.py`, referenced by the Web UI, OpenAPI (`/docs`) and diagnostics. The unmaintained `# VERSION:` / `# UPDATED:` file-header comments and unused `VERSION` constants were also removed.
- **Dynamic working folder (v1.8.0)**: the device-side transfer + delete target is now a "working folder", defaulting to `/sdcard/DCIM/NAS2GP`. It can be changed from the device page by browsing folders under DCIM (setting `DEVICE_WORK_DIR` / config schema 10). The folder is auto-created before transfer if missing. The now-unused DCIM folder-list API / method was removed.
- **Repository layout change (v1.8.0)**: Consolidated the source code under `source/`; the repository root now holds `README.md`, `LICENSE`, `docs/`, `source/` and `archive/`. `archive/` keeps a release zip (`NAS2GP_v1.8.0.zip`).

### v1.7.20 — terminology cleanup, history clear, management re-layout, flat distribution layout

- **Terminology cleanup (v1.7.20)**: dropped the non-standard abbreviation "GP" and unified UI labels and console logs on **Google Photos**.
- **Stats page "clear past history" (v1.7.20)**: a **non-destructive** filter that shows only data from a chosen point onward. The baseline is stored in the new `app_meta` table (key `stats_baseline`), and "undo clear" restores the full history. New API `POST / DELETE /api/stats/clear_history`.
- **Management page re-layout (v1.7.20)**: top section is now "DB management (open folders + data reset)", bottom section is "photo status (file list / progress)".
- **Flat distribution layout (v1.7.20)**: removed the extra `work/` level and placed `pixel_sync/`, `docs/`, `bin/`, etc. directly under the repository root. Simplified the setup steps (`WORK_ROOT` points to the repository root).

### v1.7.4 — Media Scan syntax fix / better wait UX / Google Photos refresh cycle

- Replaced the Media Scanner `for-in` syntax with a newline-delimited `while read` approach (root-caused `unexpected 'do'`).
- Improved the Google Photos backup-wait display into a human-readable summary (more readable accept rules).
- Added the "Stop -> MediaScan -> Start after push" cycle as a setting (`CYCLE_GP_BEFORE_WAIT`; default OFF, later ON).

### Early v1.7.x — batch strategy bug / console performance / settings save fixes

- Root-caused a design bug that disabled `bytes` mode.
- Improved long-running console performance.
- Fixed `BATCH_STRATEGY` reverting after settings save -> reload.

---

## 2026-08 — Major Refactor (v1.6.x)

- **v1.6.0**: Reorganized into 7 sub-packages. Introduced **size-based hybrid batch splitting**. Built the safe reset (OLD rename). Externalized i18n JSON. Added 3 diagnostics cards and language integrity checks.
- **v1.6.1**: Fixed a console log-not-shown bug. Consolidated docs from 18 to 6.
- **v1.6.2 - v1.6.5**: Root-caused missing-log bugs, made the batch UI exclusive, improved console colors.

---

## 2026-07 to 2026-08 — Desktop GUI Era (v1.5.x, removed)

> The desktop GUI (PySide6) was removed with the Web UI unification. Only highlights are kept.

- **v1.5.0**: Initial PySide6 GUI (Home / Console / Settings tabs).
- **v1.5.4**: Introduced multi-stage accept rules for the Google Photos backup wait. Fixed the inverted `scroll_to_top` swipe.

---

## 2026-05 to 2026-07 — Foundation (v1.0.0 - v1.4.x)

- Basic ADB connection (device / initial pixel / wake & unlock).
- Single-file push and the first UIAutomator dump backup detection.
- SQLite (`files` table) introduced and the 5 separate state columns established.
- Count-based batching (resumable design).

---

## Before 2026-05 (v0.1)

- Project initialization and ADB connection validation.
