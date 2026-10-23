# Settings Reference

Last updated: 2026-10-18

Settings are edited from the Web UI **Settings** page (schema-driven) and saved to `source/config/config_ui.json`.
The page items are defined by the backend `_settings_ui_schema()` and are ordered as **9 groups** that
follow the batch pipeline (stages 1-7). Each group shows an on-screen description of "which stage it
belongs to", and each item shows "what changes when you edit it", making the meaning and impact clear.

---

## 1. File Layout

```
NAS2GP/ (repository root)
├── README.md
├── LICENSE
├── .gitignore
├── .gitattributes         <- line-ending policy (v1.9.0)
├── requirements.txt       <- runtime / test dependencies (v1.9.0)
├── docs/                  <- documentation (ja / en)
├── archive/
│   └── NAS2GP_v1.9.0.zip  <- snapshot of the released version
└── source/                <- entry points, package and runtime data
    ├── app.py / entry_web.py / db_admin.py   <- entry points
    ├── nas2gp/            <- main package
    ├── bin/               <- .cmd / .sh / .command launchers
    ├── scripts/           <- helper scripts
    ├── config/
    │   ├── config_ui.json   <- persisted settings (created on first run / save)
    │   ├── consent.json     <- usage consent record (accepted time / version; not committed)
    │   ├── backups/         <- automatic DB backups (.bak.YYYYMMDD-HHMMSS, 5 generations)
    │   ├── cache/           <- temporary files
    │   └── lang/            <- external i18n language JSON (partial / full custom)
    ├── database/
    │   └── nas2gp.db    <- SQLite3 DB (production location)
    └── logs/                <- runtime logs
```

---

## 2. config_ui.json (Schema Version 10)

On first launch or when loading an older file, missing keys are auto-filled with defaults
(`config_loader._merge_missing_schema`). Older files do not cause KeyError.

```json
{
  "CONFIG_SCHEMA_VERSION": 10,
  "LANGUAGE": "auto",
  "PHOTO_DIR": "D:/Photos",
  "DEVICE_WORK_DIR": "/sdcard/DCIM/NAS2GP",
  "BATCH_STRATEGY": "hybrid",
  "MAX_FILES_PER_BATCH": 30,
  "MAX_BYTES_PER_BATCH": 2147483648,
  "DEVICE_STORAGE_MARGIN_GB": 4.0,
  "ADB_RECOVER_MIN_INTERVAL_SEC": 45.0,
  "ADB_FAILURE_POLICY": "pause_dialog",
  "ADB_RECOVERY_WAIT_SEC": 300,
  "BATTERY_LOW_THRESHOLD_PCT": 30,
  "BATTERY_CRITICAL_PCT": 15,
  "BATTERY_WAIT_MAX_SEC": 3600,
  "BATTERY_WAIT_POLL_MIN": 10,
  "BATTERY_CHECK_INTERVAL_SEC": 600,
  "DEVICE_TEMP_LIMIT_C": 45.0,
  "DISCONNECT_LOG_ENABLED": true,
  "DISCONNECT_HEARTBEAT_INTERVAL_SEC": 600,
  "BACKUP_TIMEOUT": 600,
  "BACKUP_ABSOLUTE_MAX_SECONDS": 7200,
  "MIN_BACKUP_THROUGHPUT_MBPS": 2.0,
  "BACKUP_CHECK_INTERVAL": 30,
  "MIN_ACCEPT_ELAPSED_SECONDS": 15,
  "BACKUP_STALLED_LIMIT": 5,
  "RESTART_PHOTOS_AFTER_PUSH": false,
  "LOG_VERBOSITY": "standard",
  "ENABLE_NIGHT_LIGHT_MODE": true,
  "SCREEN_BRIGHTNESS_NIGHT": 0,
  "SCREEN_OFF_WHEN_IDLE": true,
  "WEB_SERVER_HOST": "0.0.0.0",
  "WEB_SERVER_PORT": 8000
}
```

> **Note (DB path)**: the DB path is not configurable via a settings key. `settings.DB_FILE`
> (`source/database/nas2gp.db`) is the single source of truth; the writer (`Database`),
> diagnostics, and reset all reference the same real file via `settings.resolve_db_file()`.

---

## 3. Setting Groups and Items

On the Settings page, each group shows "which stage it belongs to" beneath its name, and each item
shows "what changes when you edit it". The groups below are listed in pipeline order.

### 3.1 Basic (basic) — common to all stages

| Key | Type | Default | Description |
|---|---|---|---|
| `LANGUAGE` | select | `auto` | UI / log language. `ja` / `en` / `auto` (follow OS) / an external language in `source/config/lang/*` |
| `PHOTO_DIR` | text | `D:/Photos` | Source root (NAS / SMB / local). Change it to your own photo folder |
| `DEVICE_WORK_DIR` | text | `/sdcard/DCIM/NAS2GP` | Device-side working folder (transfer + delete target). **DCIM-only**; folders under DCIM are auto-backed up by Google Photos. Also changeable from the device page |

### 3.2 (1) Capacity and batch split (CAPACITY)

Covers stage (1) Capacity and how the transfer unit (a batch) is sized. The
transfer -> backup -> delete cycle repeats per batch of this size.

| Key | Type | Default | Description |
|---|---|---|---|
| `BATCH_STRATEGY` | select | `hybrid` | Criterion to end a batch: `count` (file count) / `bytes` (size) / `hybrid` (both) |
| `MAX_FILES_PER_BATCH` | int | `30` | Max files per batch |
| `MAX_BYTES_PER_BATCH` | gb | `2` GB | Max size per batch (edited in GB) |
| `DEVICE_STORAGE_MARGIN_GB` | gb | `4.0` GB | Minimum free-space margin to keep on the device |

**Batch-end criteria**
- `count`: split by file count (`MAX_FILES_PER_BATCH`) only
- `bytes`: split by total size (`MAX_BYTES_PER_BATCH`) only
- `hybrid`: split by whichever is reached first (default)

### 3.3 (3) Upload (UPLOAD): ADB communication

ADB settings used by stage (3) Upload. They decide the per-command wait time, retries on failure,
and how to recover from a disconnect.

| Key | Type | Default | Description |
|---|---|---|---|
| `ADB_FAILURE_POLICY` | select | `pause_dialog` | Behavior on ADB failure: `pause_dialog` (pause) / `continue` (warn and continue anyway) |
| `ADB_RECOVER_MIN_INTERVAL_SEC` | float | `45.0` | Minimum interval between automatic recovery runs (seconds) |
| `ADB_RECOVERY_WAIT_SEC` | int | `300` | Max seconds to keep retrying reconnection before deciding "unrecoverable". On timeout, failed items revert to PENDING and the run stops safely |

### 3.4 (3) Upload (UPLOAD): battery / temperature / disconnect safety gate

Device protection during stage (3) Upload. When the level is low, new transfers stop and it waits
for charging; when the body temperature exceeds the limit, it waits until it cools. If neither
recovers within the cap, the run stops safely.

| Key | Type | Default | Description |
|---|---|---|---|
| `BATTERY_LOW_THRESHOLD_PCT` | int | `30` | Threshold (%) treated as low battery. Transfers pause while below it (the charging flag is not used) |
| `BATTERY_CRITICAL_PCT` | int | `15` | Absolute floor (%). Below it, the run stops safely immediately regardless of the charging state |
| `BATTERY_WAIT_MAX_SEC` | int | `3600` | Max seconds to wait on low battery / high temperature. Exceeding it stops the run safely |
| `BATTERY_WAIT_POLL_MIN` | int | `10` | Interval (minutes) to re-read the level while waiting. Temperature waits stay at a fixed 15 s to detect cooldown quickly |
| `BATTERY_CHECK_INTERVAL_SEC` | int | `600` | Interval (seconds) to check battery and temperature during transfers. Always checked before each batch |
| `DEVICE_TEMP_LIMIT_C` | float | `45.0` | Body-temperature limit (C). Above it, new transfers pause and wait for cooldown. `0` disables it |

> When a low battery is detected, transfers pause automatically and resume once the level
> recovers to the threshold. The decision uses **only the measured level**; the charging flag
> (`battery_charging`) is display-only, because a connected cable does not guarantee that the
> battery is actually charging.
> If the wait exceeds `BATTERY_WAIT_MAX_SEC`, the batch's failed items revert to `PENDING` and the
> run stops safely. The battery is also monitored on the same basis while waiting for the
> Google Photos backup to finish (stage 6); below the threshold it **waits for recovery** instead
> of stopping (unified with the wait-style gate in v1.14.2; a stop request is answered within
> ~1 second even during the wait).

> **Temperature protection (v1.14.0)**: when the body temperature exceeds `DEVICE_TEMP_LIMIT_C`,
> new transfers pause and wait for cooldown. It **resumes automatically once the temperature drops
> 3 C below the limit** (hysteresis to avoid flapping). If it does not cool within
> `BATTERY_WAIT_MAX_SEC`, the failed items revert to `PENDING` and the run stops safely.
> The temperature is checked at the same timing as the battery (batch start + every
> `BATTERY_CHECK_INTERVAL_SEC`), so no extra ADB calls are made.

### 3.5 (6) Google Photos backup wait (WAIT_BACKUP)

Covers stage (6) Google Photos Backup Wait. It decides how to wait for the photos transferred to the device to
finish uploading to the Google Photos cloud.

| Key | Type | Default | Description |
|---|---|---|---|
| `BACKUP_TIMEOUT` | int | `600` | Time treated as no-progress (seconds) |
| `BACKUP_ABSOLUTE_MAX_SECONDS` | int | `7200` | Absolute cap on the backup wait (seconds); the final ceiling for large files |
| `MIN_BACKUP_THROUGHPUT_MBPS` | float | `2.0` | Conservative assumed speed (MB/s) used to estimate the no-progress timeout from the batch size. `size / this value` becomes the lower bound of `BACKUP_TIMEOUT` (`0` disables). Prevents cutting off a single large file (e.g. a 4 GB video) whose remaining count never decreases |
| `BACKUP_CHECK_INTERVAL` | int | `30` | Interval to read on-screen text (UIAutomator dump) (seconds). One cycle is "check (screen ON + dump) + this interval", so a shorter value increases screen-on time and hinders charging. No-progress cutoff is time-based, so it is unaffected |
| `MIN_ACCEPT_ELAPSED_SECONDS` | int | `15` | Minimum elapsed time before completion can be accepted (seconds) |
| `BACKUP_STALLED_LIMIT` | int | `5` | Stall-restart threshold (consecutive polls). If "backing up" persists for this many polls, Google Photos is restarted to recover (actual time depends on `BACKUP_CHECK_INTERVAL`; e.g. 5 polls x 30s ~ 150s). Relaxed automatically in proportion to the no-progress timeout for large files (`0` disables) |
| `RESTART_PHOTOS_AFTER_PUSH` | bool | `false` | Restart Google Photos only when a backup fails |

### 3.6 Device screen (dimming during backup / turning off while idle)

Settings that dim the device screen during a backup (sync) run to reduce glare and screen burn-in.
It dims while the backup runs and restores the original brightness when it ends.
Screen brightness is independent of screenshot capture (OCR) and input, so OCR-based
backup detection and operations keep working even at brightness `0`.

Turning the screen off while idle (sections that need no screen) is a separate setting, independent
of dimming. During transfers, battery/temperature recovery waits, and between backup status checks,
the screen is kept off. Google Photos keeps uploading even with the screen off.

| Key | Type | Default | Description |
|---|---|---|---|
| `ENABLE_NIGHT_LIGHT_MODE` | bool | `true` | Dim the screen during backup |
| `SCREEN_BRIGHTNESS_NIGHT` | int | `0` | Brightness during backup (0-255). Restored to the original level automatically when the run ends |
| `SCREEN_OFF_WHEN_IDLE` | bool | `true` | Turn the device screen off during sections that need no screen (transfers / battery & temperature recovery waits / between backup status checks). Google Photos keeps uploading even with the screen off. Recommended ON, because a device stopped on low battery charges much more slowly while the screen stays lit. When OFF, only dimming as before applies |

### 3.7 Analysis log (disconnect analysis + stats data)

Settings for the log that periodically records the device state (level / temperature / free space)
during long transfers. This record is used both to analyze disconnect causes (snapshots in
`source/logs/disconnect_*.log`) and as the source data for the time-series graphs on the Stats page
(battery / temperature / free space, in `source/logs/health.jsonl`). Turning it ON only when
investigating or collecting data is enough.

| Key | Type | Default | Description |
|---|---|---|---|
| `DISCONNECT_LOG_ENABLED` | bool | `true` | Record the analysis log (disconnect analysis + stats data). Turning it OFF stops recording, so the stats time series stops updating too |
| `DISCONNECT_HEARTBEAT_INTERVAL_SEC` | int | `600` | Recording interval (seconds). Smaller records finer detail but increases adb calls. Effective only while the checkbox above is ON |

### 3.8 Log display

Controls how much log is shown on the screen (console). The default "standard" is fine for normal use.

| Key | Type | Default | Description |
|---|---|---|---|
| `LOG_VERBOSITY` | select | `standard` | Log volume: `full` / `standard` / `minimal` |

### 3.9 Web UI

Public settings for this Web screen itself. Changes require restarting the app (`entry_web.py`).

| Key | Type | Default | Description |
|---|---|---|---|
| `WEB_SERVER_HOST` | text | `0.0.0.0` | Bind address. `0.0.0.0` exposes it to the whole LAN. The header shows the real LAN IP |
| `WEB_SERVER_PORT` | int | `8000` | Listen port. Auto-falls back to 8001+ if in use |

> Keys that require a restart (HOST / PORT, etc.) take effect from the next launch.
> The save action reports this via an inline notice.

**"Usage consent" card at the bottom of the Settings page**: it shows the consent state (accepted / not
accepted) and the accepted time, and a red "Reset consent" button clears the consent record
(`config/consent.json`). After a reset, the consent overlay (full-screen confirmation) reappears the
next time you open a page.

---

## 4. Adding an External i18n Language JSON

Steps to add a language other than Japanese/English (e.g. German `de`) and customize the wording.

### Step 1. Create `source/config/lang/de.json`

```json
{
  "label": "Deutsch (DE)",
  "words": {
    "web.btn.start": "Starten",
    "web.btn.stop": "Anhalten",
    "web.nav.settings": "Einstellungen"
  }
}
```

> **Partial translation OK**: writing only some keys in `words` is enough. Unwritten keys
> keep the built-in JA (bottom layer), so the UI never breaks.

### Step 2. Detection and selection

At startup (`i18n.list_available_languages()`) it is auto-detected and **Deutsch (DE)** appears in
the Settings language dropdown. Selecting and saving reloads the page, retranslating the whole UI.

### Step 3. (Optional) Integrity check

The Diagnostics page's language card shows missing keys / duplicates / external JSON info.
You can also check via CLI (run with the `source` folder as the current directory):

```powershell
python -c "from nas2gp.core import i18n; print(i18n.diagnose_wordbank('de'))"
```

---

## 5. Management Operations

The Web UI **Management** page consists of two parts:

1. **File list / progress**: filter, search, and sort the transfer-target files by their state
   (transfer / Google Photos backup / delete) on a per-column basis (moved from the old **Stats** page).
2. **Action buttons**: 3 OPEN buttons and 3 RESET buttons.

```
[Open DB folder]  [Open settings folder]  [Open logs folder]
[Reset progress]  [Reset settings]  [Factory reset]
```

> Analysis such as transfer volume, speed, and item-count trends is consolidated on the **Stats**
> page, which is now graph-only; the file list has moved to the Management page.

### 5.1 OPEN buttons

Simply open the target folder in the OS file manager (nothing is deleted).

### 5.2 RESET buttons (safe OLD rename)

`rm` is never used. Files are renamed with an `_OLD_YYYYMMDD_HHMMSS_` prefix, so a mistake is recoverable.

| Button | Operation | How to recover |
|---|---|---|
| Reset progress | Move the DB file + WAL/SHM journal + old backup DBs to OLD | Remove the prefix from the filename |
| Reset settings | Move `source/config/config_ui.json` to OLD | same as above |
| Factory reset | Move everything in the DB folder + settings + past logs (`*.log`) to OLD | same as above |

> Important operations such as factory reset use a 2-3 step inline confirmation to prevent mistakes.

### 5.3 DB Maintenance CLI

Run the following with the `source` folder as the current directory.

```powershell
# integrity check
python -m nas2gp.storage.db_debug

# VACUUM + 5-generation backup
python -m nas2gp.storage.db_maintenance

# force-reset only ADB-error rows to PENDING
python db_admin.py reset-failed

# reset transfer / Google Photos / delete state
python db_admin.py reset-transfer
python db_admin.py reset-google
python db_admin.py reset-delete

# retry failed rows / aggregate
python db_admin.py retry-failed
python db_admin.py summary
python db_admin.py failed

# help
python db_admin.py --help
```

---

## 6. Tuning Guide

### 6.1 Photo-heavy library (many files / low size)

Use a larger `MAX_FILES_PER_BATCH` (e.g. 50) with a smaller `MAX_BYTES_PER_BATCH`, or set
`BATCH_STRATEGY = count`. This drains each batch faster and reduces the number of waits.

### 6.2 Video-heavy library (few files / high size)

Use `BATCH_STRATEGY = bytes` or `hybrid` and split by `MAX_BYTES_PER_BATCH`.
Each video round-trips through transfer -> backup -> delete, preventing device storage exhaustion.

### 6.3 Unstable ADB

Keep `ADB_FAILURE_POLICY = pause_dialog` to inspect the cause.
If disconnects are frequent, a larger `ADB_RECOVERY_WAIT_SEC` (e.g. 600 = 10 min) improves the
auto-recovery success rate.

### 6.4 Tuning the backup-complete decision

The fastest approach is to watch the accept status live on the **Console** page.

- Too slow: decrease `MIN_ACCEPT_ELAPSED_SECONDS`
- Too many false positives (too early): increase `MIN_ACCEPT_ELAPSED_SECONDS` / adjust `BACKUP_ABSOLUTE_MAX_SECONDS`
- Cut off on a large file (e.g. a single multi-GB video): lower `MIN_BACKUP_THROUGHPUT_MBPS` (extends the timeout) / increase `BACKUP_TIMEOUT`
- Google Photos restarting repeatedly on a large file (stall-restart false trigger): increase `BACKUP_STALLED_LIMIT`. When the no-progress timeout is extended it is relaxed proportionally, so the default is usually fine

### 6.5 Long unattended runs (guarding against the phone's battery running out)

During long overnight runs, transfers can stop if the phone's battery runs out. The following
settings provide automatic recovery and safe stop.

- `BATTERY_LOW_THRESHOLD_PCT`: threshold treated as low battery (default 30%). Waits while below it (the charging flag is not used)
- `BATTERY_CRITICAL_PCT`: absolute floor (default 15%). Below it the run stops safely immediately regardless of the charging state
- `BATTERY_WAIT_MAX_SEC`: wait limit (default 3600 = 60 min). Exceeding it stops safely
- `BATTERY_WAIT_POLL_MIN`: interval to re-read the level while waiting (default 10 min)
- `BATTERY_CHECK_INTERVAL_SEC`: battery check interval during transfers (default 600 = 10 min)
- `DEVICE_TEMP_LIMIT_C`: body-temperature limit (default 45 C). Above it, transfers pause and wait for cooldown, resuming 3 C lower. `0` disables it
- `ADB_RECOVERY_WAIT_SEC`: recovery wait limit after a disconnect (default 300 = 5 min)

> **Note**: USB power usually prevents battery depletion. If the device is fully powered OFF, ADB
> cannot recover it; physical charging and powering it back on are required. This software never
> turns the device off (it only does screen-off and reboot).

### 6.6 Analyzing the cause of a disconnect (analysis log)

To diagnose a disconnect that happened during a long evaluation run, keep `DISCONNECT_LOG_ENABLED`
enabled (the default). Because `source/logs/health.jsonl` keeps the battery trend, you can tell whether the
disconnect was a "gradual battery depletion" (level dropping step by step) or a "sudden disconnect"
(plenty of charge left = cable / ADB failure) from the trend. A smaller
`DISCONNECT_HEARTBEAT_INTERVAL_SEC` records finer detail, at the cost of more frequent recording
(adb calls).

> This analysis log also serves as the source data for the time-series graphs on the Stats page
> (battery / temperature / free space). Turning it OFF stops the stats time series from updating,
> so keep it ON whenever you use either analysis or stats.
