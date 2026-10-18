# ============================================
# FILE: nas2gp/settings.py
#
# User Settings
# 基本的にユーザーが編集するのはこのファイルだけです。
# This is basically the only file that users need to edit.
# ============================================

from pathlib import Path
import os as _os

# 製品 バージョン 。 Web 画面 / OpenAPI(/docs) / 診断 が 参照 する 唯一 の 定義 です 。
# 更新 する とき は この 1 箇所 だけ を 変更 し て ください 。
# Product version: the single source of truth referenced by the Web UI, OpenAPI (/docs)
# and diagnostics. When releasing, update only this one place.
APP_VERSION = "1.13.4"

# --------------------------------------------
# Work Root (v1.7.4 追加: 診断 / リセット など の 「DB ファイル 相対パス 解決」 を 共通化)
# Work Root (v1.7.4: shared absolute-path resolution of the DB file for diagnostics/reset, etc.)
#   settings.py の ある フォルダ を 起点 → nas2gp/ → 親 = work フォルダ を 絶対パス で 保持。
#   Starts from settings.py's folder -> nas2gp/ -> parent = work folder, kept as an absolute path.
#   これ に より 診断 タブ / 進捗 リセット から の CWD 非依存 な ファイル 参照 が 可能 に なる。
#   This enables CWD-independent file references from the diagnostics tab / progress reset.
# --------------------------------------------
WORK_ROOT = Path(__file__).resolve().parent.parent.parent
if not WORK_ROOT.exists():
    try:
        WORK_ROOT = Path(_os.getcwd()).resolve()
    except Exception:
        WORK_ROOT = Path.cwd().resolve()

# --------------------------------------------
# Photo Library
# --------------------------------------------

PHOTO_DIR = Path("D:/Photos")


# --------------------------------------------
# Database
# --------------------------------------------

DB_FILE = "database/nas2gp.db"


def resolve_db_file() -> Path:
    """書き込み側 Database が 実際 に 開く DB ファイル の 絶対パス を 返す。
    DB パス は DB_FILE の 1 箇所 を 正 と し 診断 / リセット も ここ を 参照 する。
    相対 パス の 場合 は WORK_ROOT 配下 として 解決 する。

    Return the absolute DB path actually opened by the writer (Database).
    DB_FILE is the single source of truth; diagnostics/reset reference it too.
    A relative path is resolved under WORK_ROOT.
    """
    p = Path(str(DB_FILE))
    if p.is_absolute():
        return p
    return (Path(str(WORK_ROOT)) / p).resolve()


# v1.8.0: 旧 DB 名 (製品名 改称 前)。移行 判定 の ため のみ 保持 する。
# v1.8.0: legacy DB filename (before the product rename). Kept only for migration.
LEGACY_DB_FILE = "database/pixel_sync.db"


def migrate_legacy_db_file() -> str | None:
    """v1.8.0: 旧 DB 名 を 新 DB 名 へ 一度 だけ 移行 する (データ 引き継ぎ)。

    新 DB (nas2gp.db) が まだ 無く 、 旧 DB (nas2gp.db) が 存在 する 場合 のみ rename する。
    WAL / SHM ジャーナル も 併せて 移す。戻り値 は 移行 し た 場合 の 説明 、 不要 なら None。

    v1.8.0: one-time migration of the legacy DB filename (data carry-over).
    Renames only when the new DB is absent and the legacy DB exists, moving the
    WAL / SHM journals too. Returns a description when migrated, else None.
    """
    new_p = resolve_db_file()
    if new_p.exists():
        return None
    old_rel = Path(str(LEGACY_DB_FILE))
    old_p = old_rel if old_rel.is_absolute() else (Path(str(WORK_ROOT)) / old_rel).resolve()
    if not old_p.exists():
        return None
    moved = []
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(old_p) + suffix)
        if src.exists():
            dst = Path(str(new_p) + suffix)
            src.replace(dst)
            moved.append(Path(src).name)
    return f"{old_p} -> {new_p} ({', '.join(moved)})"


# --------------------------------------------
# 端末 側 の 作業フォルダ (v1.8.0)
# Device-side working folder (v1.8.0)
# --------------------------------------------

# 端末 へ 写真 を 転送 し 、 Google Photos の バックアップ 完了 後 に 削除 する フォルダ 。
# DCIM 配下 に する こと で Google Photos の 自動 バックアップ 対象 と なる 。
# 端末 ページ から 変更 可能 。 安全 の ため DCIM 配下 のみ 許可 する 。
# The folder where photos are transferred and, after Google Photos backup, deleted.
# Keeping it under DCIM makes it a Google Photos auto-backup target.
# Changeable from the device page; only paths under DCIM are allowed for safety.
DEVICE_WORK_DIR = "/sdcard/DCIM/NAS2GP"


# --------------------------------------------
# ADB (v1.5 追加)
# ADB (added in v1.5)
# --------------------------------------------

ADB_TIMEOUT = 120
ADB_PUSH_RETRY = 3
ADB_SHELL_RETRY = 3
ADB_RECONNECT_INTERVAL = 60


# --------------------------------------------
# Night Light Mode (常夜灯モード, v1.5 追加)
# Night Light Mode (night-light mode, added in v1.5)
# --------------------------------------------

ENABLE_NIGHT_LIGHT_MODE = True
SCREEN_BRIGHTNESS_NIGHT = 0
SCREEN_BRIGHTNESS_DEFAULT = 80
SCREEN_OFF_TIMEOUT_MAX = 2147483647


# --------------------------------------------
# Upload
# --------------------------------------------

# バッチサイズ: 10 -> 30 に拡大 (v1.5 スループット改善)
# Batch size: increased 10 -> 30 (v1.5 throughput improvement)
MAX_FILES_PER_BATCH = 30

# バッチサイズ (容量ベース, bytes): 0 = 無効 (ファイル数だけで batch 切り)
# Byte-based batch size (bytes): 0 = disabled (split by file count only)
# 推奨 2GB = 2 * 1024**3 bytes。ファイル数と容量 どちらか 先に到達したほうで切る (1-B)
# Recommended 2GB = 2 * 1024**3 bytes; split when either count or size is reached first (1-B)
MAX_BYTES_PER_BATCH = 2 * 1024**3

# バッチあたり容量の GUI 表示用 推奨プリセット (GB単位)
# Recommended presets (GB) for the GUI display of per-batch capacity
MAX_BYTES_PER_BATCH_PRESETS_GB = [1, 2, 4, 8, 16]

# バッチ分割モード (v1.6.2 追加 / Settings Tab 排他 ラジオボタン 連動):
# Batch split mode (v1.6.2, linked to the exclusive radio buttons in the Settings tab):
#   "count" : MAX_FILES_PER_BATCH だけで切る (容量制限 無効)
#   "count" : split by MAX_FILES_PER_BATCH only (size limit disabled)
#   "bytes" : MAX_BYTES_PER_BATCH だけで切る (ファイル数制限 実質無効)
#   "bytes" : split by MAX_BYTES_PER_BATCH only (file-count limit effectively disabled)
#   "hybrid" (推奨 DEFAULT v1.7.7 以降): MAX_FILES_PER_BATCH と MAX_BYTES_PER_BATCH の 両方 OR 条件で 先に 到達 し た 方 で 切る。
#   "hybrid" (recommended default since v1.7.7): split on whichever of count/size is reached first (OR condition).
#            (旧来 の count 固定 では MAX_BYTES を 正しく 効か ない ため デフォルト を hybrid に 変更 。
#            (Default changed to hybrid because the old count-only mode did not apply MAX_BYTES correctly.)
BATCH_STRATEGY = "hybrid"

# 端末 空き容量 チェック時 の 安全マージン (GB):
# Safety margin (GB) for the device free-space check:
#   バッチサイズ + この マージン を 空き容量 が 下回る と DEVICE_STORAGE_INSUFFICIENT イベント 発火
#   Fires DEVICE_STORAGE_INSUFFICIENT when free space drops below batch size + this margin.
DEVICE_STORAGE_MARGIN_GB = 4.0

# ADB kill-server / start-server 再接続 処理 の 最小 実行 間隔 (秒):
# Minimum interval (sec) for ADB kill-server / start-server reconnection:
#   0 秒 = 制限 なし (不安定 な 接続 向け / 推奨 10〜20 秒)。
#   0 sec = no limit (for unstable connections; 10-20 sec recommended).
#   低く する と 復旧 は 早く なる が ADB サーバー 負荷 上昇。 高く する と 復旧 遅い が 安定。
#   Lower = faster recovery but higher ADB-server load; higher = slower recovery but more stable.
ADB_RECOVER_MIN_INTERVAL_SEC = 45.0

# ADB 再接続 全失敗 時 の 挙動 (v1.7.9 新規):
# Behavior when all ADB reconnection attempts fail (new in v1.7.9):
#   "pause_dialog" (推奨 default): ADB_DEVICE_UNRECOVERABLE イベント を publish し
#   "pause_dialog" (recommended default): publishes ADB_DEVICE_UNRECOVERABLE,
#     GUI が ダイアログ 表示 → ユーザ USB 抜き差し / スマホ 再起動 → OK 押下 で 再試行。
#     the GUI shows a dialog -> user reconnects USB / reboots the phone -> OK to retry.
#     バッチ 処理 は 安全 に 一時停止 (ここ で 待つ)。
#     Batch processing pauses safely here and waits.
#   "continue"          : WARNING だけ 出し て バッチ 処理 を 続行 (旧来 の 挙動 、 削除 が 一部 失敗 し て も 進める)。
#   "continue"          : warns only and continues the batch (legacy; proceeds even if some deletes fail).
ADB_FAILURE_POLICY = "pause_dialog"

# --------------------------------------------
# ADB 切断復旧 / バッテリー監視 (v1.7.18 新規)
# ADB disconnect recovery / battery monitoring (new in v1.7.18)
# --------------------------------------------

# 切断 検知 後 、 「復旧 不能」 と 判断 する まで 再接続 を 粘る 上限 秒数 (既定 300 = 5分)。
# 切断 判定 は 転送 失敗 時 に のみ 行う (毎 ファイル では 確認 せ ず 軽量 化)。
# この 間 aggressive reconnect を 繰り返し 、 復旧 すれ ば 転送 を 続行 、
# 復旧 し なけれ ば その バッチ の FAILED を PENDING に 戻し て 安全 停止 する 。
# Upper bound (sec) to keep retrying reconnection after a disconnect is detected
# (default 300 = 5 min). Detection runs only on transfer failure (not per file, for lightness).
# On recovery, transfer resumes; on timeout, the batch's
# FAILED rows are reverted to PENDING and the run stops safely.
ADB_RECOVERY_WAIT_SEC = 300

# バッテリー 残量 が これを 下回り 、 かつ 充電 中 で なけれ ば 新規 転送 を 一時 停止 する (% )。
# Pause new transfers when the battery level drops below this value and the device is not charging (%).
BATTERY_LOW_THRESHOLD_PCT = 20

# 低残量 で 一時 停止 し た 後 、 充電 / 残量 回復 を 待つ 上限 秒数 (既定 3600 = 60分)。
# これを 超える と 安全 停止 する (FAILED を PENDING に 戻す)。
# Upper bound (sec) to wait for charging / recovery after pausing on low battery
# (default 3600 = 60 min). Past this, the run stops safely (FAILED reverted to PENDING).
BATTERY_WAIT_MAX_SEC = 3600

# バッテリー 残量 を 監視 する 間隔 秒 (既定 600 = 10分)。各 バッチ 開始 前 に は 必ず 確認 する 。
# Battery monitoring interval in seconds (default 600 = 10 min). Always checked before each batch.
BATTERY_CHECK_INTERVAL_SEC = 600

# --------------------------------------------
# 切断解析ログ (v1.7.18 新規): ハートビート + 切断時 スナップショット
# Disconnect analysis log (new in v1.7.18): heartbeat + disconnect snapshot
# --------------------------------------------

# 有効 時 、 転送 中 に 端末 状態 を 定期 記録 (logs/health.jsonl) し 、
# 切断 を 検知 し た とき に 解析 用 スナップショット (logs/disconnect_*.log) を 出力 する 。
# When enabled, periodically records device state during transfers (logs/health.jsonl)
# and writes an analysis snapshot (logs/disconnect_*.log) when a disconnect is detected.
DISCONNECT_LOG_ENABLED = True

# ハートビート の 記録 間隔 秒 (既定 600 = 10分)。30 未満 は 30 に 丸める 。
# Heartbeat recording interval in seconds (default 600 = 10 min). Values below 30 are clamped to 30.
DISCONNECT_HEARTBEAT_INTERVAL_SEC = 600

# --------------------------------------------
# Web UI (FastAPI)
# --------------------------------------------
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 8000
WEB_MAX_LOG_LINES = 120
WEB_SSE_INTERVAL_SECONDS = 1.0


# --------------------------------------------
# Google Photos
# --------------------------------------------

# バックアップ無進捗タイムアウト (秒): v1.7.15 で 「全体 の 経過 時間」 から
# Backup no-progress timeout (sec): in v1.7.15 changed from total elapsed time
#   「進捗 が 一切 なかっ た 時間」 基準 へ 変更 。 残り 枚数 / ETA の 減少 や
#   to a "time with no progress at all" basis. The timer resets when remaining count / ETA decreases
#   complete の 新規 検出 が あれ ば タイマー は リセット さ れ る ため 、
#   or a new "complete" is detected, so
#   大容量 ファイル の 長時間 バックアップ を 誤って 打ち切ら ない 。
#   long backups of large files are not cut off by mistake.
# v1.11.1: config (config_loader) の 既定 600 に 合わせ て 一元化 した。
#   実行時 は config_ui.json の 値 が settings へ 上書き さ れる (Web / CLI 共通)。
# v1.11.1: unified to the config default (600); at runtime the config value
#   overrides this constant for both the Web UI and the CLI.
BACKUP_TIMEOUT = 600

# 絶対上限 (秒): 進捗 の 有無 に 関わら ず これを 超え た ら 打ち切る (無限 待機 防止)。 v1.7.15 追加。
# Absolute upper limit (sec): aborts once exceeded regardless of progress (prevents infinite wait). Added in v1.7.15.
BACKUP_ABSOLUTE_MAX_SECONDS = 7200

# v1.11.0: 無進捗 タイムアウト を バッチ サイズ に 比例 さ せる ため の 「保守的 な 想定
#   バックアップ 速度」(MB/s)。 バッチ 合計 サイズ を この 速度 で 割った 時間 を 無進捗
#   タイムアウト の 下限 と する こと で 、 残り 枚数 が 減ら ない 単一 大容量 ファイル
#   (例: 4GB の 動画) の 長時間 バックアップ を 誤って 打ち切ら ない 。
#   0 以下 に する と この 延長 は 無効 (従来 の 固定 BACKUP_TIMEOUT のみ で 判定)。
# v1.11.0: Conservative assumed backup speed (MB/s) used to scale the no-progress
#   timeout with the batch size, so long backups of a single large file (e.g. a 4 GB
#   video) are not cut off while the remaining count stays flat.
#   Set to 0 or less to disable this scaling (fixed BACKUP_TIMEOUT only).
MIN_BACKUP_THROUGHPUT_MBPS = 2.0

# v1.11.1: 停滞 リスタート の しきい値 (連続 回数)。「バックアップ中」 が 監視 ループ で
#   この 回数 連続 し た ら Google Photos を 再起動 して 立て直す 。 実時間 は 監視 間隔
#   (BACKUP_CHECK_INTERVAL) に 依存 する (例: 15 回 × 8 秒 ≒ 120 秒)。 大容量 バッチ 時 は
#   無進捗 タイムアウト に 比例 させ て 自動 で 緩和 さ れる (worker/backup_waiter.py)。
#   0 以下 で 停滞 リスタート を 無効 化 。
# v1.11.1: Stall-restart threshold (consecutive polls). Time is derived from
#   BACKUP_CHECK_INTERVAL. Relaxed proportionally for large batches. <=0 disables.
BACKUP_STALLED_LIMIT = 15

# 監視ループ間隔 (秒)。v1.11.1: config (config_loader) の 既定 8 に 合わせ て 一元化 した。
#   実行時 は config_ui.json の 値 が settings へ 上書き さ れる (Web / CLI 共通)。
# Monitoring loop interval (sec). v1.11.1: unified to the config default (8); at
#   runtime the config value overrides this constant for both Web UI and CLI.
BACKUP_CHECK_INTERVAL = 8


# --------------------------------------------
# Backup Accept Rules (v1.5.4 追加: 大幅緩和 第3弾 - ユーザー実機ログフィードバック適用)
# Backup Accept Rules (added in v1.5.4: major relaxation round 3, based on user device-log feedback)
#   旧: MIN_ACCEPT=45s が長すぎて 1回目complete (29s) を逃し 2回目まで29s無駄待ち → 是正
#   Old: MIN_ACCEPT=45s was too long, missing the first "complete" (29s) and wasting 29s until the second -> fixed
# --------------------------------------------

# 受理判定に必要な minimum 経過秒数: 45 -> 15 秒 (大幅短縮!)
# Minimum elapsed seconds required for acceptance: 45 -> 15 sec (greatly shortened!)
MIN_ACCEPT_ELAPSED_SECONDS = 15

# strict (Aルール) 経由なしで受理する complete 連続回数: 既に 1
# Consecutive "complete" count accepted without the strict (rule A) path: already 1
MIN_COMPLETE_STREAK = 1

# recent window 内の complete 割合しきい値: 0.4 (timeout時更に緩和)
# "complete" ratio threshold within the recent window: 0.4 (relaxed further on timeout)
RECENT_WINDOW = 10
COMPLETE_QUORUM_RATIO = 0.3

# Force Ultra (最終保険Dルール): 120s/3hits -> 80s / 3hits (短縮)
# Force Ultra (last-resort rule D): 120s/3hits -> 80s/3hits (shortened)
FORCE_ULTRA_ELAPSED_SECONDS = 80
FORCE_ULTRA_MIN_COMPLETE_HITS = 3

# Ultra Safe (Zルール) 専用: complete1回観測 + この秒数経過で受理 (旧 FORCE*0.85=102s → 新 40s)
# For Ultra Safe (rule Z): accept on one observed "complete" + this elapsed time (old FORCE*0.85=102s -> new 40s)
ULTRA_SAFE_ELAPSED_SECONDS = 40

# Complete初回出現からの経過秒数 (Gルール): 最速受理ルート、1回complete出てからたったこの秒数でOK
# Elapsed seconds since the first "complete" (rule G): fastest accept route, OK after just this many seconds
FIRST_COMPLETE_ACCEPT_DELAY_SECONDS = 8

# backoff / スタック検知: complete はスタック対象から除外
# backoff / stuck detection: "complete" is excluded from stuck targets
STACK_TARGET_STATUSES = frozenset(["unknown", "tutorial", "lock_screen"])
MAX_BACKUP_RETRY = 2


# --------------------------------------------
# Language
#   auto = OS locale に従う (Windows 設定/日本語環境なら ja)
#   auto = follow the OS locale (ja on a Japanese Windows environment)
#   ja   = 日本語固定
#   ja   = fixed to Japanese
#   en   = 英語固定
#   en   = fixed to English
# --------------------------------------------

LANGUAGE = "auto"


# --------------------------------------------
# Scan & Transfer (v1.5 追加)
# Scan & Transfer (added in v1.5)
# --------------------------------------------

SCAN_INTERVAL_SECONDS = 3600
ENABLE_SMB_SESSION_KEEPALIVE = True
SMB_KEEPALIVE_INTERVAL = 180


# --------------------------------------------
# Device Storage
# --------------------------------------------

# この容量を下回る転送は行わない
# Do not transfer when free space falls below this value
MIN_FREE_SPACE_GB = 2


# --------------------------------------------
# Upload Batch (旧設定, 互換性のため残す)
# Upload Batch (legacy setting, kept for compatibility)
# --------------------------------------------

UPLOAD_BATCH_SIZE = 1
# --------------------------------------------
# Google Photos restart policy (v1.5.4 NEW 原則OFF + failsafe条件付き再起動)
# Google Photos restart policy (v1.5.4 NEW: OFF by default + failsafe conditional restart)
#
# 問題: 旧版では転送バッチの直後に毎回 Google Photos を force-stop していた
# Problem: the old version force-stopped Google Photos after every transfer batch,
#       → 端末側の Google Photos が転送された写真を検知し クラウドアップロード開始直後に
#         プロセスが強制終了されるため、アップロードが失敗・重複・遅延する問題が多発
#         so GP detected the transferred photo and was killed just as cloud upload began,
#         causing frequent upload failures, duplicates and delays.
# 方針: 通常再起動は一切しない (OFF)
# Policy: never restart normally (OFF);
#       万が一 wait_backup_complete がタイムアウト / recovery が閾値超過 した場合のみ
#       failsafe として 次回のバッチ開始前または 監視中 の recovery シーケンス で 1 回だけ再起動
#       only as a failsafe, when wait_backup_complete times out or recovery exceeds the threshold,
#       restart once before the next batch or during the monitoring recovery sequence.
# --------------------------------------------
RESTART_PHOTOS_AFTER_PUSH = False

# failsafe 再起動を発動する recovery 閾値 (1バッチ内で recovery がこの回数以上になったら次回バッチ前に再起動)
# Recovery threshold that triggers a failsafe restart (restart before the next batch once in-batch recovery count reaches this)
FAILSAFE_RECOVERY_THRESHOLD_PER_BATCH = 3

# タイムアウトで backup_ok = False 時 failsafe 再起動を実施するか (推奨 True)
# Whether to perform a failsafe restart when backup_ok = False due to timeout (True recommended)
FAILSAFE_RESTART_ON_BACKUP_TIMEOUT = True

# --------------------------------------------
# ★ v1.7.4 新機能: 端末 側 の Google Photos メディア 反映 を 確実 に する ため の Stop → Start ループ
# ★ v1.7.4 new: Stop -> Start cycle to ensure device-side Google Photos reflects new media
# ユーザー から の 指摘: 「アップロード → 端末 → Google Photos の 順に ファイル が 反映 さ れる べき だ が、
#                       Google Photos が 裏 で 居座っ た まま だ と キャッシュ / 古い 状態 を 維持 し て
#                       ストレージ に 新規 ファイル が あっ て も クラウド アップロード を 開始 し ない 時 が ある」
# User report: files should sync upload -> device -> Google Photos in order, but if GP keeps
#              running in the background it holds a stale cache and may not start uploading new files.
# 対応 策: 1 バッチ の 写真 を 端末 へ 転送 し た 直後、
#         ① Google Photos を am force-stop で 完全 停止 → ② Media Scan 実行 → ③ Monkey + scroll_to_top で GP 再起動
#         → ④ wait_backup へ 入る こと で、GP に 新規 メディア を 強制 的 に 再認識 さ せる。
# Fix: right after transferring a batch to the device, 1) fully stop GP via "am force-stop",
#      2) run Media Scan, 3) restart GP via Monkey + scroll_to_top, 4) enter wait_backup to force GP to re-detect new media.
#   CYCLE_GP_BEFORE_WAIT = False (DEFAULT) : 旧挙動 維持 (Stop/Start し ない。従来 通り の 処理)
#   CYCLE_GP_BEFORE_WAIT = False (DEFAULT) : keep legacy behavior (no Stop/Start, processing as before)
#   CYCLE_GP_BEFORE_WAIT = True            : 上記 Stop → MediaScan → Start サイクル を 各バッチ で 実行
#   CYCLE_GP_BEFORE_WAIT = True            : run the above Stop -> MediaScan -> Start cycle for each batch
# ※ RESTART_PHOTOS_AFTER_PUSH (旧設定互換) と は 独立 し て い ます。
# ※ Independent of RESTART_PHOTOS_AFTER_PUSH (legacy compat):
#    RESTART_PHOTOS_AFTER_PUSH = wait_backup *終了後* の failsafe restart (backup timeout/recovery閾値のみ発火)
#    RESTART_PHOTOS_AFTER_PUSH = failsafe restart *after* wait_backup (fires only on backup timeout/recovery threshold)
#    CYCLE_GP_BEFORE_WAIT  = wait_backup *開始前* の 能動的 な Stop/Start サイクル (ユーザー が ON/OFF 切替)
#    CYCLE_GP_BEFORE_WAIT  = active Stop/Start cycle *before* wait_backup (user toggles ON/OFF)
# --------------------------------------------
CYCLE_GP_BEFORE_WAIT = True
CYCLE_GP_DELAY_AFTER_START_SECONDS = 5

# --------------------------------------------
# Scroll log level (v1.5.4 ログ削減):
# Scroll log level (v1.5.4 log reduction):
#   0 = MINIMAL  : 画面 scroll_to_top の実行ログは1行のprintに圧縮 + ADB FAIL/RETRY のみ常時。
#   0 = MINIMAL  : compress scroll_to_top logs into a single print line; always show ADB FAIL/RETRY only.
#                  方向説明/開始/完了/[SCROLL-START]など長いprintは非表示 (DEFAULT 推奨)
#                  Hide long prints like direction description/start/done/[SCROLL-START] (recommended default)
#   1 = NORMAL   : _maybe_scroll_to_top の選択 print / scroll_to_top の開始・完了 1行ずつ表示
#   1 = NORMAL   : show the _maybe_scroll_to_top selection print and one line each for scroll start/done
#   2 = VERBOSE  : 全ての方向説明 / ADB 成功ラベル を含む完全なprint (デバッグ専用、前回デフォルト)
#   2 = VERBOSE  : full prints including all direction descriptions and ADB success labels (debug only, previous default)
# --------------------------------------------
SCROLL_LOG_LEVEL = 0

# --------------------------------------------
# ★ v1.7.5 新機能: Log Verbosity (ログ 詳細 度 全体 切替)
# ★ v1.7.5 new: Log Verbosity (global log-detail switch)
#   ユーザー 要望 「全ログ / 標準 / 最小 を ボタン で 切替 可能 に し て 最小 時 は Scroll TOP 表示 し ない」
#   User request: switch full/standard/minimal via buttons, and hide Scroll TOP when minimal.
#   LOG_VERBOSITY は SCROLL_LOG_LEVEL より 優先 さ れ ます:
#   LOG_VERBOSITY takes priority over SCROLL_LOG_LEVEL:
#     "minimal"  : 最 小 限 の ログ のみ。backup_waiter の Scroll TOP 2 行 (実行中 / OK) は 完全 非 表示。
#         "minimal"  : minimal logs only; the backup_waiter Scroll TOP 2 lines (running / OK) are fully hidden.
#                  DEBUG BAR は GP 進捗 行 (+ 待機理由 1 行) だけ に 絞る。
#                  The DEBUG BAR shows only the GP progress line (+ one wait-reason line).
#                  → 内部 的 に SCROLL_LOG_LEVEL を -1 扱い (更なる 抑制) と し て 解釈。
#                  -> Internally SCROLL_LOG_LEVEL is treated as -1 (extra suppression).
#     "standard" : 現状 v1.7.4 / v1.7.5 初期 設定 と 同じ。SCROLL_LOG_LEVEL の 値 を そのまま 尊重。
#         "standard" : same as the v1.7.4 / early v1.7.5 defaults; respects SCROLL_LOG_LEVEL as-is.
#                  → Scroll TOP 要約 1 行 あり / DEBUG BAR は GP 進捗 行 + 待機理由 のみ
#                  -> One Scroll TOP summary line; the DEBUG BAR shows the GP progress line + wait reason only.
#     "full"     : 旧 VERBOSE 2 互換。SCROLL_LOG_LEVEL を 強制 的 に 2 = VERBOSE に 上書き。
#         "full"     : compatible with old VERBOSE 2; forces SCROLL_LOG_LEVEL to 2 (VERBOSE).
#                  → Scroll TOP 方向 / ADB 成功 label まで 全 て の print を 有効 化 (デバッグ 時)
#                  -> Enables all prints including Scroll TOP direction and ADB success labels (for debugging).
#   v1.7.15: 受理 条件 ヒント (最速 G / 標準 B / 最終保険 Z) と 30s ハートビート は
#   v1.7.15: accept-condition hints (fastest G / standard B / last-resort Z) and the 30s heartbeat
#            ノイズ の ため 全 レベル で 非 表示 に 統一 。
#            are hidden at all levels to reduce noise.
# --------------------------------------------
LOG_VERBOSITY = "standard"
LOG_VERBOSITY_ALLOWED = ("full", "standard", "minimal")

def get_effective_scroll_log_level():
    """LOG_VERBOSITY と SCROLL_LOG_LEVEL を 合成 し て 実効 レベル を 返す。
    Returns:
        int: -1 = minimal (scroll print 一切 なし) / 0 = standard compressed / 1 = normal / 2 = verbose full

    Combines LOG_VERBOSITY and SCROLL_LOG_LEVEL and returns the effective log level.
    """
    v = LOG_VERBOSITY
    if isinstance(v, str):
        lv = v.lower()
    else:
        lv = "standard"
    if lv == "minimal":
        return -1
    if lv == "full":
        return 2
    # standard: 既存 SCROLL_LOG_LEVEL を そのまま 返す (0/1/2 の いずれ か)
    # standard: return the existing SCROLL_LOG_LEVEL as-is (one of 0/1/2)
    try:
        s = int(SCROLL_LOG_LEVEL)
    except Exception:
        s = 0
    return s if s in (0, 1, 2) else 0


# --------------------------------------------
# ★ v1.7.5 新機能: Push/ADB 長時間 安定 化 (エラー 多発 根本 対策)
# ★ v1.7.5 new: Push/ADB long-run stabilization (root fix for frequent errors)
#
# 問題: 転送 中 の USB 通信 の 揺らぎ や 端末 側 adbd の 一時 停止 で 「device offline」
#       が 頻発 し 1 バッチ 1 ファイル 失敗 する だけ で 大量 の WARNING/ERROR ログ が 発生 し
#       ユーザー 体験 を 著しく 損ね て いる。
# Problem: USB flakiness during transfers and temporary adbd stops on the device cause frequent
#          "device offline" errors; one failed file in a batch spams WARNING/ERROR logs and hurts UX.
#
# 対策:
# Measures:
#  (1) ADB.reconnect 内 の 再接続 試行 を 段階 的 に エスカレーション:
#  (1) Escalate reconnection attempts inside ADB.reconnect in stages:
#       Level 1 : 通常 poll 待ち
#       Level 1 : normal poll wait
#       Level 2 : N 回 毎 に kill-server + start-server + adb reconnect
#       Level 2 : every N attempts, kill-server + start-server + adb reconnect
#       Level 3 : 最終 手段 で adb reconnect offline + 端末 画面 wakeup (prepare_ui 呼び出し)
#       Level 3 : last resort, adb reconnect offline + device screen wakeup (prepare_ui)
#  (2) UploadWorker 内 の 単一 ファイル リトライ 回数 を 3 → 5 へ 増やし、
#      リトライ 間隔 も 1s,3s,5s,8s,12s と 段階 的 に 増やす。
#  (2) Increase single-file retries in UploadWorker from 3 to 5, with escalating waits 1s,3s,5s,8s,12s.
#  (3) flow 側 の 「1 ファイル FAIL → バッチ FAIL」 判定 を 緩和:
#      同 一 バッチ 内 で FAIL し た ファイル が 1 件 だけ で 、 かつ 直前 の 数 バッチ が 成功 し て いれ ば
#      バッチ ステータス は DONE を 維持 し 再 転送 で 次回 処理 を 任せる。
#  (3) Relax the flow's "1 file FAIL -> batch FAIL" rule: if only one file failed in the batch and the
#      previous few batches succeeded, keep the batch status DONE and retry on the next run.
# --------------------------------------------

# (1) UploadWorker 内部 の 1 ファイル push リトライ 回数 / 間隔
# (1) Single-file push retry count / intervals inside UploadWorker
PUSH_INNER_RETRY = 5
PUSH_INNER_RETRY_WAITS = [1.0, 3.0, 5.0, 8.0, 12.0]

# (2) 再接続 エスカレーション 閾値 (ADB.reconnect aggressive_kill_every)
# (2) Reconnection escalation thresholds (ADB.reconnect aggressive_kill_every)
ADB_KILL_EVERY_PUSH = 3     # push 内 reconnect: 3 回 毎
# push reconnection: every 3 times
ADB_KILL_EVERY_SHELL = 2    # shell 内 reconnect: 2 回 毎
# shell reconnection: every 2 times

# (3) flow 側 バッチ FAIL 緩和 閾値
# (3) Flow-side batch-FAIL relaxation threshold
FLOW_FAIL_THRESHOLD_BATCH_FAIL_COUNT = 1   # この 件数 「以下」 の FAIL なら バッチ FAIL に し ない
# Do not fail the batch if the FAIL count is at or below this number


