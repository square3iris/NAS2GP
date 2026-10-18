# Roadmap & TODO

Last updated: 2026-10-18

---

## 1. Overview

```
Phase 1  ADB foundation        done  (2026-05)
Phase 2  DB (SQLite)           done  (2026-06)
Phase 3  single-file push      done  (2026-06)
Phase 4  batching (by count)   done  (2026-07)
Phase 5  desktop GUI           done -> removed for Web UI unification (2026-10)
Phase 6  GUI feature work       done -> removed for Web UI unification (2026-10)
Phase 7  Web UI unification     done  (2026-10)  <- current
Phase 8  distribution & upkeep  in progress (main focus of the next release)
Phase 9  cloud integration      undecided
Phase 10 IoT multi-device        undecided
```

---

## 2. Completed Milestones

| Phase | Content | Release |
|---|---|---|
| 1 | Basic ADB connection / device / initial pixel / wake & unlock | v0.1 |
| 2 | SQLite introduced / 5 separate state columns decided | v0.5 |
| 3 | Single-file push + initial UIAutomator dump backup detection | v1.0.0 |
| 4 | Count-based batching / resumable design | v1.4 |
| 5-6 | Desktop GUI (PySide6) built and enhanced -> **removed** | v1.5-v1.6 |
| 7 | Desktop GUI removed -> **Web UI unification** (FastAPI + Jinja2 + SSE) / 7 pages / JA-EN i18n / stats filters / diagnostics & management pages | v1.7.x (current) |
| 7+ | Long-run safety gates (battery / ADB disconnect detection & staged recovery) / disconnect analysis log (health.jsonl) / stats page turned analysis-only with 5 time-series graphs (range 24h/7d/30d) / settings restructured into 9 pipeline-order groups / terminology cleanup (GP -> Google Photos), stats past-history clear, management page re-layout, flat distribution layout | v1.7.18-v1.7.20 |

---

## 3. Phase 8 — Next Development (ROADMAP)

With Web UI unification complete, this phase strengthens distribution and operation.

### 3.1 Highest priority
- [ ] **Single-file distribution (PyInstaller)**: build `entry_web.py` into an `.exe`. Auto-create `config/` and `database/` on first launch. Include fastapi / uvicorn / jinja2 hidden imports, an icon, and either bundle or auto-download `adb.exe`.
- [ ] **Update checker**: on startup, check the latest tag on GitHub Releases (or a NAS) and show an inline notice in the Web UI. No forced auto-update.

### 3.2 High priority
- [ ] **Parallel sync for multiple devices**: turn the serial setting into a list and start a sync worker thread per device. Make the UI switch between devices.
- [ ] **Wireless ADB (Wi-Fi)**: auto connect / disconnect via `adb tcpip 5555` + `adb connect`. Add an IP section to Settings.
- [ ] **Strict pre-transfer hashing (SHA-256)**: implement hash-based de-duplication (add a DB column).

### 3.3 Medium priority
- [ ] **Move deleted files to trash**: move to a temporary area instead of `rm`, with a configurable restore window.
- [ ] **Visualize backup accept rules**: color-code the accept status.

### 3.4 Low priority
- [ ] **Web server residency / auto-start**: register for startup and launch automatically after login.
- [ ] **External notification webhook**: notify Slack / Teams / LINE on completion.
- [ ] **Disk free-space monitoring**: warn on the Diagnostics page when the PHOTO_DIR volume is low.

---

## 4. Future Ideas (Backlog)

| ID | Idea | Note | Priority |
|---|---|---|---|
| 9-1 | Cloud integration | NAS -> cloud only, as a secondary backup | Low |
| 9-2 | Multiple library profiles | profile switching / separate DBs | Medium |
| 9-3 | Frontend enhancement | keep FastAPI + Jinja2 + SSE, enhance as needed | Low |
| 9-4 | Similar-photo detection | propose candidates via image features (never auto-delete) | Low |
| 10-1 | IoT multi-device | centrally manage multiple devices + NAS | Future |

---

## 5. Known Issues (Open)

### 5.1 Performance
- [ ] Investigate device-dependent failures when pushing very large files (>2GB) in chunks.
- [ ] Additional on-device testing of Media Scan escaping for filenames with parentheses on Android 14+.

### 5.2 UX
- [ ] Clearly color-code which backup accept rules fired.
- [ ] First-run wizard (PHOTO_DIR selection / device connection check / ADB setup guidance).

### 5.3 Testing / CI
- [ ] CI (GitHub Actions): lint / `py_compile` / ja-en key parity / DB unit tests / mock ADB tests.
- [ ] Pseudo End-to-End tests using a mock ADB.

### 5.4 Security / Operations
- [ ] Toggle to disable UI Automation for users who want to wait manually.
- [ ] Document how to restrict the source segment when wireless ADB is enabled.
