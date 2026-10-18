# Changelog — Summary

Last updated: 2026-10-18

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
