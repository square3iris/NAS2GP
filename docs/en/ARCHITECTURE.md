# Architecture

Last updated: 2026-10-18

NAS2GP transfers photos and videos from a NAS to a device over USB (ADB),
waits for the Google Photos app to finish its standard backup, and then deletes
the local copies from the device. All interaction is provided through a single
**Web UI** (the legacy desktop GUI has been removed).

---

## 1. Overall Structure (Layered + Web UI)

```
          [Windows PC browser / console]
                     |
                     v
   entry_web.py / app.py / db_admin.py   (thin entry points)
                     |
                     v
   +================================================================+
   |        pixel_sync (main package - 7 sub-packages)               |
   +================================================================+
    | Layer 1: core/ (shared foundation)
    |   settings.py / logger.py / util.py / config.py / config_loader.py
    |   events.py / control.py / maintenance.py / diagnostics.py
    |   disconnect_log.py (disconnect analysis: heartbeat / snapshot)
    |   i18n.py (i18n + externalized word bank)  /  lang/{ja,en}.py
    |
    | Layer 2: scanner/ (local file scan) + storage/media.py
    |   scanner/local.py  (PHOTO_DIR walk + de-duplication)
    |
    | Layer 3: device/ (device / ADB)  <- focus of stability & performance
    |   adb.py    (retry / auto-reconnect / shell probe liveness / health)
    |   ui.py     (wake / unlock / scroll_to_top / prepare_ui)
    |   pixel.py  (push + batched scan_media_batch)
    |
    | Layer 4: worker/ (sync flow control)
    |   sync_flow.py      (INIT/SCAN/UPLOAD/WAIT/DELETE/FINALIZE)
    |   backup_waiter.py  (Google Photos backup-complete waiting / accept rules)
    |
    | Layer 5: uploader/ (batch split & transfer)
    |   flow.py  (_build_batches : count / bytes / hybrid)
    |
    | Layer 6: storage/ (SQLite)
    |   db.py              (schema / 5 separate state columns)
    |   db_batch.py        (batch status updates)
    |   db_stats.py        (speed calc excluding backup wait)
    |   db_view.py         (read-only layer mode=ro / stats & listing)
    |   db_debug.py        (integrity check)
    |   db_maintenance.py  (VACUUM / backup)
    |
    | Layer 7: ui_web/ (FastAPI + Jinja2 + SSE)
    |   main.py            (create_app / routing / SSE / settings schema)
    |   templates/         (7 pages)
    +================================================================+
                     |
          +----------+-----------+
          v                      v
   [NAS/SMB photo share]    [device internal storage (USB ADB)]
          |                      |
          v                      v
   folder scan & de-dupe     batched ADB push
                          Media Scanner broadcast
                          UIAutomator dump (on-screen text)
                          Google Photos standard backup
```

---

## 2. Sub-package Structure

| Package | Role | Main files |
|---|---|---|
| `pixel_sync.core/` | Shared foundation | `settings.py`, `logger.py`, `i18n.py`, `util.py`, `config.py`, `config_loader.py`, `events.py`, `control.py`, `maintenance.py`, `diagnostics.py`, `disconnect_log.py`, `lang/{ja,en}.py` |
| `pixel_sync.device/` | device operations / ADB abstraction | `adb.py`, `ui.py`, `pixel.py` |
| `pixel_sync.scanner/` | Local file scanning | `local.py` |
| `pixel_sync.storage/` | DB layer | `db.py`, `db_batch.py`, `db_stats.py`, `db_view.py`, `db_debug.py`, `db_maintenance.py`, `media.py` |
| `pixel_sync.uploader/` | Batch transfer logic | `flow.py` |
| `pixel_sync.worker/` | Sync flow control | `sync_flow.py`, `backup_waiter.py` |
| `pixel_sync.ui_web/` | Web UI (FastAPI + Jinja2 + SSE) | `main.py`, `templates/*.html` |

---

## 3. Sync Phases

### 3.1 Macro Phases (worker/sync_flow.py)

```
INIT      DB migration / integrity check / ADB connection check / apply settings
          register atexit + signal (= always screen OFF on exit)

SCAN      recursive walk of PHOTO_DIR -> extension whitelist -> de-dupe by size+mtime
          SKIP performs no DB write (minimizes writes). DB backup on completion.

UPLOAD    batch split (count / bytes / hybrid) -> ADB push -> Media Scan

WAIT      wait for Google Photos backup completion (on-screen text based)

DELETE    delete only files whose backup is confirmed complete

FINALIZE  print stats summary / DB VACUUM / device screen OFF
```

### 3.2 The 7 Steps Within a Batch (matches the Web UI step indicator)

`uploader/flow.py` publishes step numbers via `_publish_phase()` (phase_no = 1..7 / total_phases = 7).

| # | Step | Description |
|---|---|---|
| 1 | `CAPACITY` | Ensure capacity (check free space before the batch and run GC if needed) |
| 2 | `GP_STOP` | (optional) First half of the Google Photos stop cycle |
| 3 | `UPLOAD` | Transfer via ADB push + DB update |
| 4 | `MEDIA_SCAN` | Run the Media Scanner broadcast in one shot |
| 5 | `GP_START` | (optional) Start Google Photos + scroll_to_top |
| 6 | `WAIT_BACKUP` | Wait for Google Photos backup completion |
| 7 | `DELETE` | Delete confirmed files from the device |

Auxiliary steps `GC` (before a batch) and `RECOVER` (during recovery) are also published via `_publish_phase()`.

### 3.3 Automatic Recovery (RECOVER)

When transfer or connection failures continue, it recovers in up to 3 loops (`_AUTO_RECOVER_MAX = 3`):

```
S1: re-run GC (re-ensure free space / delete NAS2GP-transferred files)
S2: reboot the device (reboot_device, wait 45s after reboot)
```

- v1.8.0: the "delete all of /sdcard/DCIM" step, which risked wiping personal photos, was removed; only per-file deletion of NAS2GP-transferred files (S1) remains.
If unresolved after 3 loops, it stops safely to avoid infinite loops.

---

## 4. DB Schema (files table) — 5 Separate State Columns

Each pipeline step keeps its own state in an independent column. There is no single `status` column.

| Column | Type | Values | Role |
|---|---|---|---|
| `nas_state` | TEXT | `NEW` / `UPDATED` | Source (NAS/local) state |
| `phone_transfer_state` | TEXT | `PENDING` / `DONE` / `FAILED` | ADB transfer step |
| `google_photos_state` | TEXT | `PENDING` / `DONE` / `FAILED` | Google Photos backup |
| `phone_delete_state` | TEXT | `PENDING` / `DONE` / `FAILED` | device file deletion step |
| `verify_state` | TEXT | `PENDING` / `DONE` / `FAILED` | Post verification (future use) |

- Main companion columns: `path`, `nas_path`, `filename`, `extension`, `size`, `mtime`, `media_type`,
  `transferred_at`, `backup_started_at`, `backup_completed_at`, `deleted_at`, `verified_at`,
  `retry_count`, `last_error`, `created_at`, `updated_at`
  - `transferred_at` is recorded when a transfer completes (`phone_transfer_state='DONE'`, v1.7.19).
    The per-hour aggregates on the stats page (transfer GB/hour, file-count trend) use this column.
- `batch_history` table: per-batch records (`started_at`, `completed_at`, `file_count`,
  `total_bytes`, `elapsed_seconds`, `upload_seconds`, `backup_seconds`, `status`, etc.).
  Capacity-based volume and effective speed are also aggregated from this table for past data.
- Indexes: on `phone_transfer_state` / `google_photos_state` / `phone_delete_state` /
  `transferred_at` (`idx_files_transferred_at`, added in v1.7.19)

> Upload targets are determined by `WHERE phone_transfer_state='PENDING'`
> (separated from `nas_state`, which keeps resume logic simple).

### 4.1 DB Location

The production database is `database/nas2gp.db` (the folder is tracked via `database/.gitkeep`).
Stats and listing always use the read-only connection in `db_view.py` (`mode=ro` + `busy_timeout`)
to avoid contention with the writer process.

---

## 5. Web UI Architecture (FastAPI + Jinja2 + SSE)

### 5.1 Page Layout (7 pages, shared `base.html`)

| Path | Template | Description |
|---|---|---|
| `/` | `index.html` | Home: start / stop / stop after this batch / status banner / step indicator / connected device |
| `/console` | `console.html` | Console: recent logs (SSE) / level filter |
| `/settings` | `settings.html` | Settings: schema-driven (9 groups in pipeline order + descriptions) / language switch |
| `/pixel` | `pixel.html` | device: device details / manual operations |
| `/management` | `management.html` | Management: file list (independent per-column filters + paging) + 3 OPEN + 3 RESET (OLD rename + multi-step confirm) |
| `/stats` | `stats.html` | Stats (analysis only): range switch (24h / 7d / 30d) + time-series graphs (volume, count, speed, battery, temperature) |
| `/diagnostics` | `diagnostics.html` | Diagnostics: device info / DB state / language integrity / bug report ZIP |

### 5.2 Backend -> Frontend Propagation

| Channel | Content | Notes |
|---|---|---|
| SSE `/progress/sse` | phase changes / progress / stats / device info / Google Photos progress | via `core/events.py`; default 1s interval |
| SSE `/api/logs/sse` | console logs (last 300 lines -> realtime) | `_WebTee` + Logger web capture |
| Poll `/api/status` | sync state / batch number / stats summary | on demand |
| Poll `/api/adb/devices` | connected device state (green/yellow/grey) / health | 30s interval |
| Control API | start / stop / stop after batch / resets | via flags in `core/control.py` |

> `_WebTee` intercepts `sys.stdout/stderr`, filters out lines already emitted by `Logger`,
> and streams the rest to SSE.

### 5.3 Main API Endpoints

```
GET  /api/status                      sync state / progress
GET  /api/adb/devices                 connected device list + state
GET  /api/adb/device_info             device details
GET  /api/settings                    current settings
PUT  /api/settings                    save settings
GET  /api/settings/schema             settings UI schema
GET  /api/config/schema               config schema info
GET  /api/logs                        recent logs
GET  /api/logs/sse                    log SSE
GET  /api/files                       file list (filter / paging)
GET  /api/files/summary               stats summary
GET  /api/stats/timeseries            stats time-series (range 24h/7d/30d, DB + health.jsonl)
GET  /api/diagnostics                 diagnostics info
GET  /api/diagnostics/bugreport       generate bug report ZIP
POST /api/maintenance/reset_progress  reset progress
POST /api/maintenance/reset_settings  reset settings
POST /api/maintenance/factory_reset   factory reset
GET  /api/pixel/folders               device folder list
POST /api/pixel/delete_uploaded       delete only NAS2GP-transferred files (safe)
POST /api/open_path                   open a folder
POST /api/start                       start sync
POST /api/stop                        stop immediately
POST /api/stop_batch                  stop after this batch
GET  /progress/sse                    progress SSE
```

---

## 6. i18n Architecture (3-layer word bank composition)

```
Bottom layer (master / guaranteed 100%):  pixel_sync.core.lang.ja.WORDS
          ↓  missing keys ultimately fall back to this
Middle layer (override when English):    pixel_sync.core.lang.en.WORDS
          ↓  partial / full override
Top layer (user custom):                 config/lang/{lang_code}.json
          { "label": "Display name", "words": { "web.btn.start": "Start", ... } }
```

- **Partial translation OK**: writing only some keys in `words` leaves the rest to the lower layers (ultimately JA), so the UI never breaks.
- **Dynamic scan**: `i18n.list_available_languages()` globs `config/lang/*.json` and adds them to the language list at startup / on settings save.
- **Diagnostics API**: `i18n.diagnose_wordbank(code)` returns missing-key counts / JA-EN key parity / duplicates / external JSON info, shown on the diagnostics language card.
- **Schema merge**: `config_loader._merge_missing_schema()` fills missing keys with defaults to prevent KeyError.
- Frontend JS reads `window.I18N` (the same dictionary as templates) to keep wording consistent.

---

## 7. Design Principles / Hard Constraints

| Constraint | Description | Location |
|---|---|---|
| No SJIS emojis | Avoids Windows CMD UnicodeEncodeError. No emojis in console output. | all console output |
| Screen OFF on exit | Always issue screen-off on both atexit and signal (SIGINT/SIGTERM). | core/util atexit |
| Web log capture | Stream stdout/stderr + Logger to SSE for the console page. | `ui_web.main._WebTee` |
| Safe reset = OLD rename | Rename with `_OLD_YYYYMMDD_HHMMSS_` prefix instead of `rm`; recoverable after mistakes. | `core/maintenance.py` |
| Read-only DB access | Stats/listing use `mode=ro` + `busy_timeout`. | `storage/db_view.py` |
| i18n key parity | When adding log/UI keys, add to both `ja.py` and `en.py`. | `core/lang/*.py` |
| Staged recovery on low space | GC (delete NAS2GP-transferred files) -> reboot, up to 3 loops, then force stop if unresolved. Bulk DCIM deletion was removed to protect personal photos. | `uploader/flow.py` recovery |
| Two-tier backup wait | No-progress timeout plus an absolute cap `BACKUP_ABSOLUTE_MAX_SECONDS` (default 7200s). | `worker/backup_waiter.py` |
