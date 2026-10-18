# ============================================
# FILE: nas2gp/config.py
#
# Developer Settings
# 基本的に変更不要
# Basically: no changes needed
# ============================================

# Upload
# アップロード設定
# Upload settings

MAX_RETRY = 3
RETRY_WAIT = 1
COMMIT_INTERVAL = 20


# Storage Management
# ストレージ管理
# Storage management

# 削除後にここまで回復したら転送再開
# Resume upload after free space reaches this value

TARGET_FREE_SPACE_GB = 5


# Scan
# スキャン対象拡張子
# File extensions to scan

SUPPORTED_EXTENSIONS = {

    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",

    ".heic",
    ".heif",

    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
    ".mts",
    ".m2ts",
    ".mkv",

}