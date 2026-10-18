# Operation Guide

Last updated: 2026-10-18

Setup, launch, daily operation, maintenance, troubleshooting and recovery are consolidated into this single file.

---

## Table of Contents

1. [Requirements](#1-requirements)
2. [Initial Setup](#2-initial-setup)
3. [How to Launch (Web / CLI)](#3-how-to-launch-web--cli)
4. [Daily Operation Flow](#4-daily-operation-flow)
5. [Monthly Maintenance](#5-monthly-maintenance)
6. [Troubleshooting FAQ](#6-troubleshooting-faq)
7. [Recovery Procedures](#7-recovery-procedures)

---

## 1. Requirements

### 1.1 PC

| Item | Required | Recommended |
|---|---|---|
| OS | Windows 10 (22H2+) / macOS / Linux | Windows 11 (23H2+) |
| OS (macOS / Linux) | best-effort (unverified on real macOS hardware) | - |
| Python | 3.11 | 3.12 |
| RAM | 4 GB | 8 GB or more (for scanning very large libraries) |
| Free disk | 20 GB | 100 GB or more |
| USB | USB 2.0 or higher | USB 3.2 Gen1 / device stock cable |
| ADB | Android Platform Tools 34+ | latest |
| Web UI deps | `fastapi` / `uvicorn` / `jinja2` | latest |

> **macOS / Linux (best-effort)**: it can launch on macOS / Linux, but has not been verified on real macOS hardware.
> Install ADB via Homebrew with `brew install --cask android-platform-tools`, or download the Android platform-tools manually and put `adb` on your PATH.

### 1.2 device

| Item | Requirement |
|---|---|
| Model | Google Pixel 3 or newer (Android 12+) |
| Recommended | Pixel 6 or newer / Android 14+ |
| Setting 1 | Developer options ON -> USB debugging ON -> "Allow USB debugging" on connect |
| Setting 2 | Latest Google Photos / signed in to the target account / backup quality configured |
| Setting 3 | Screen lock (PIN / pattern) configured (used by wake / unlock) |
| Setting 4 | (Optional, recommended) operate while charging |

---

## 2. Initial Setup

### Step 1. Verify Python + pip

```powershell
python --version    # Python 3.11.x / 3.12.x
pip --version       # pip 24+
```

If missing, get Python 3.12 x64 from https://www.python.org/downloads/windows/ and
enable "Add python.exe to PATH" during install.

### Step 2. Install ADB and add to PATH

1. Get the Windows ZIP from https://developer.android.com/tools/releases/platform-tools
2. Extract to `C:\tools\platform-tools\`
3. Add `C:\tools\platform-tools` to the PATH environment variable
4. Verify in a new PowerShell:

```powershell
adb version
# -> Android Debug Bridge version 1.0.41 ...
```

### Step 3. Configure the device

1. Settings -> About phone -> tap Build number 7 times -> enable Developer options
2. Settings -> System -> Developer options -> USB debugging = ON
3. Connect to the PC with the device stock USB cable
4. On the device prompt "Allow USB debugging?" -> "Always allow" + Allow
5. Verify:

```powershell
adb devices
# -> List of devices attached
# -> XXXXXX123456  device      <- if "unauthorized", step 4 was not done
```

### Step 4. Install dependencies

```powershell
cd <folder where NAS2GP is placed>\source

# virtual environment (recommended)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install --upgrade pip
pip install uvicorn fastapi jinja2
```

### Step 5. First Web UI launch + basic settings

```powershell
python entry_web.py
```

On the first launch, opening `http://127.0.0.1:8000/` in a browser shows a full-screen **Usage consent** (disclaimer) overlay. Read the warning and tick the two checkboxes (understanding of data deletion / agreement at your own risk); the "Accept and start" button becomes clickable. The acceptance is recorded in `source/config/consent.json` and is not shown again.

Then configure on the **Settings** page:

1. **PHOTO_DIR**: the photo folder (e.g. `D:\Photos`, or `\\NAS\Photos` for a NAS)
2. **Batch strategy / batch size / max batch size (GB)**: for photo-heavy libraries prefer a larger file count and smaller size; for video-heavy, prefer size-first
3. **Language**: Japanese / English (or an external language added under `source/config/lang/`)
4. Click **Save**

### Step 6. Test run with a few files

1. Place a few JPGs under PHOTO_DIR
2. **Home -> Start**
3. Watch progress (the step indicator advances through the 7 steps)
4. Confirm all cards are healthy (green) on the **Diagnostics** page

---

## 3. How to Launch (Web / CLI)

Run with the **`source` folder** as the current directory.

```powershell
cd <folder where NAS2GP is placed>\source
```

### Web UI (normal case)

```powershell
python entry_web.py          # run directly (recommended)
bin\start_web_app.cmd        # via the .cmd launcher
```

- On startup, the console prints `http://127.0.0.1:8000/`.
- From phones / other PCs on the LAN: `http://<PC IP>:8000/`.
- Port is `WEB_SERVER_PORT` (default 8000). If in use, it auto-falls back to 8001 and up.
- Stop with **Ctrl+C** (even if browser SSE connections remain, it force-stops within 3 seconds).

### CLI (for scheduled tasks / automation)

```powershell
python app.py             # run directly (recommended)
bin\start_app.cmd         # via the .cmd launcher
```

It uses the settings in `source/config/config_ui.json` automatically and prints phase progress and errors to stdout.

### Launching on macOS / Linux (best-effort)

On macOS / Linux use the `.sh` / `.command` launchers (not verified on real macOS hardware).

```bash
# install dependencies (run from the repository root)
pip install -r requirements.txt

# launch the Web UI
cd <folder where NAS2GP is placed>/source
python3 entry_web.py             # run directly
bin/start_web_app.sh             # via the .sh launcher
```

- On macOS you can also double-click `bin/start_web_app.command` from Finder (it delegates to the matching `.sh`).
- For the CLI, run `python3 app.py` or `bin/start_app.sh`.
- The launchers use `.venv/bin/python` if present, otherwise `python3`.

---

## 4. Daily Operation Flow

```
[Start]
 1. Boot the PC -> confirm the device is connected via USB
 2. Run python entry_web.py -> open http://127.0.0.1:8000/ in a browser
 3. Confirm every card is healthy (green) on the Diagnostics page
 4. Home page -> Start
 5. Minimize the browser and work on the PC (errors appear in the home status banner)

[Stop]
 6. Check the Console page for WARN / ERROR
 7. Check the Stats page for transferred volume / effective speed / count trends (range: 24h / 7d / 30d)
    (per-file details and filters are on the Management page)
 8. "Stop" or "Stop after this batch" -> Ctrl+C in the console -> the device screen turns off automatically
```

- **Stop**: interrupts the running process immediately.
- **Stop after this batch**: finishes the current batch, then stops safely.
- **Redo the consent**: press "Reset consent" in the "Usage consent" card at the bottom of the Settings page, or delete `source/config/consent.json`. The consent confirmation reappears the next time you open a page.

---

## 5. Monthly Maintenance

Recommended: a fixed day each month (e.g. the first Sunday).

1. **Check DB backups**: automatic backups keep 5 generations under `source/config/backups/`. Remove old `.bak` files as you like.
2. **Manual VACUUM**: Management page -> open the DB folder -> run `python -m nas2gp.storage.db_maintenance`
3. **Check size**: if `source/database/nas2gp.db` is large, increase VACUUM frequency.
4. **Update ADB / Platform Tools**: about once every six months is enough.
5. **Language integrity**: check the Diagnostics page for new missing keys (if any, add them to the relevant JSON `words`).

---

## 6. Troubleshooting FAQ

### 6.1 Startup / ADB Connection

| No | Symptom | Likely cause (in order) | Fix |
|---|---|---|---|
| 1 | `adb devices` shows `unauthorized` | USB debugging permission denied / PC key mismatch | Developer options -> revoke USB debugging authorizations -> reconnect -> allow again |
| 2 | `adb devices` shows `no devices` / `offline` | bad cable / charge-only cable / USB setting | use the stock cable / set USB to "File transfer" / `adb kill-server ; adb start-server` |
| 3 | `No module named 'fastapi' / 'uvicorn' / 'jinja2'` at startup | Web UI deps not installed | activate `.venv` and run `pip install uvicorn fastapi jinja2` |
| 4 | `No module named 'nas2gp'` at startup | current directory is not the `source` folder | `cd` into the `source` folder before launching |

### 6.2 SCAN / DB

| No | Symptom | Cause | Fix |
|---|---|---|---|
| 5 | "PHOTO_DIR does not exist" during scan | PHOTO_DIR missing / no UNC access | reconnect the network drive -> reselect PHOTO_DIR on Settings |
| 6 | The same image is transferred repeatedly | NAS changed size / mtime so de-dupe fails | Management -> reset progress (hash detection planned) |
| 7 | `database is locked` | CLI and Web access the same DB simultaneously | do not run the Web UI and CLI at the same time |
| 8 | DB corrupted / SELECT errors | sudden PC shutdown / bad disk | restore the newest `.bak` from `source/config/backups/` to `source/database/nas2gp.db` |

### 6.3 UPLOAD / ADB push

| No | Symptom | Cause | Fix |
|---|---|---|---|
| 9 | push FAILED then succeeds after retry | unstable USB connection | use a stock USB 3.2 cable / a rear port on the PC |
| 10 | Media Scanner misses files whose names contain parentheses | sh escaping on some devices | reduce max batch size (GB) to lower files per batch |
| 11 | Delete runs prematurely when free space drops below the threshold | by design | avoid sending large videos in bulk / increase the margin GB |
| 12 | Pushed files do not appear in Google Photos | Media Scan lag | reboot the device / run "Back up now" in Google Photos |

### 6.4 WAIT_BACKUP

| No | Symptom | Cause | Fix |
|---|---|---|---|
| 13 | stuck at `backing_up`, never `complete` | no network / Wi-Fi not connected | connect the device to Wi-Fi (mobile data is slow) |
| 14 | deleted before backup actually completed (misjudged) | scroll not at TOP / on-screen text mismatch | tighten conditions (increase `MIN_ACCEPT_ELAPSED_SECONDS`) / inspect accept rules in the console |
| 15 | `unknown` occurs often | lock screen / charging animation / toast overlap | Developer options -> "Stay awake"/"ADB while sleeping" ON |
| 16 | wait never ends | Google Photos paused / upload failed | check the app and force-stop/restart if needed (auto-escapes via no-progress timeout and absolute cap) |

### 6.5 DELETE / FINALIZE

| No | Symptom | Cause | Fix |
|---|---|---|---|
| 17 | `rm` returns Permission denied | device security | Developer options -> USB debugging (security settings) ON |
| 18 | duplicate files remain after deletion | Google Photos recreates device-side copies (Google behavior) | expected; ignore. If tight, delete manually / increase margin GB |
| 19 | device screen stays ON after exit | signal killed / forced kill | exit via the Stop button or Ctrl+C (avoid force-kill from Task Manager) |

### 6.6 Logs / Language

| No | Symptom | Cause | Fix |
|---|---|---|---|
| 20 | no logs on the console page | SSE (`/api/logs/sse`) not connected | reload the browser / confirm the server is running |
| 21 | important logs missing | level filter too narrow | set the filter back to ALL |
| 22 | settings lost after restart | save permission of `source/config/config_ui.json` / repository folder read-only | check ownership / NTFS permissions of the repository folder |
| 23 | some text stays in the wrong language after switching | missing i18n keys (detected on Diagnostics) | check Diagnostics language card -> add keys to the external JSON `words` |
| 24 | cannot access from another PC / phone | bound to `127.0.0.1` / firewall | set `WEB_SERVER_HOST = 0.0.0.0` -> restart / allow port 8000 |
| 25 | reset does not seem to take effect | OLD rename keeps the data (correct behavior) | open the target folder on Management and manually rename back from `_OLD_` files |

### 6.7 Performance

| No | Symptom | Cause | Fix |
|---|---|---|---|
| 26 | scan is slow | recursive walk over a huge library / NAS latency | enable SMB 3.1.1 / run at night / run right after VACUUM |
| 27 | average speed looks wrong | speed used to include backup wait time | current version computes from transfer time; upgrading/refreshing the DB corrects it |
| 28 | device becomes sluggish from many Media Scans | too many files per batch | lower batch size and switch to size-first |

### 6.8 Other

| No | Symptom | Cause | Fix |
|---|---|---|---|
| 29 | DB grew huge | long-term use without VACUUM | Management -> factory reset (safe; an OLD backup is created) |
| 30 | console mojibake in a Japanese environment | code page 932 | set "Use Unicode UTF-8" in system locale -> reboot the PC |

---

## 7. Recovery Procedures

### Case A: DB corrupted, SELECT fails

1. Stay calm: your NAS originals are untouched. Worst case is a full re-send.
2. Open `source/config/backups/` and pick the newest healthy backup (`.bak.YYYYMMDD-HHMMSS`).
3. Fully exit the app.
4. Move `source/database/nas2gp.db` aside and rename the copy from step 2 to `source/database/nas2gp.db`.
5. Restart. Already-SKIPped files are skipped and unsent `PENDING` files resume.

### Case B: Factory Reset accidentally executed

1. Stay calm: it uses OLD rename, not `rm`, so it is recoverable.
2. Management -> open the DB folder / settings folder.
3. `_OLD_YYYYMMDD_HHMMSS_nas2gp.db` -> drop the prefix to restore `nas2gp.db`.
4. Do the same for `_OLD_*_config_ui.json`.
5. Restart to return to the previous state.

### Case C: Sync frozen (no progress for a long time)

1. Press **Stop** in the Web UI (sends a stop signal to the worker).
2. If it does not stop within 30 seconds, press Ctrl+C in the `entry_web.py` console.
3. If still stuck, end one `python.exe` from Task Manager.
4. Before restarting, always check `adb devices`; if `offline`, run `adb reconnect offline` -> `adb kill-server ; adb start-server`.
5. Restarting resumes from where it left off (safe design: re-run from `PENDING`).
