# ============================================================
# nas2gp Top-level __init__.py (v1.6 サブパッケージ化 互換レイヤー)
#
# 目的: 旧コード `from nas2gp.ui import scroll_to_top` のような
#       フラットなimport文 を壊さないための re-export レイヤー。
#       新規コードからは `from nas2gp.device.ui import scroll_to_top`
#       のように サブパッケージ絶対import を推奨。
# ============================================================
# ============================================================
# nas2gp Top-level __init__.py (compat layer for the v1.6 subpackage split)
#
# Purpose: a re-export layer so legacy flat imports such as
#          `from nas2gp.ui import scroll_to_top` keep working.
#          New code should prefer subpackage absolute imports like
#          `from nas2gp.device.ui import scroll_to_top`.
# ============================================================

# ============================================================
# 【互換レイヤー 前半】旧モジュールパス import を先に sys.modules に登録
# 理由: from X import * の後で register すると __dict__ 属性取得だけ
# 成功し `import nas2gp.db` module としての lookup に失敗することがある。
# 旧名 import を 100% 透過にするため先に sys.modules alias を登録。
# ============================================================
# ============================================================
# [Compat layer, first half] Register legacy module paths in sys.modules first.
# Reason: registering after `from X import *` can make attribute lookup succeed
# while `import nas2gp.db` module lookup fails. Register the sys.modules
# aliases up front so legacy-name imports are 100% transparent.
# ============================================================
import sys as _sys

def _register_alias(alias_name: str, real_module):
    """sys.modules に旧名=新実体 を登録

    Register the legacy alias name to the real module in sys.modules.
    """
    _sys.modules[f"nas2gp.{alias_name}"] = real_module

# 2. Device
import nas2gp.device.adb as _mod_adb      ; _register_alias("adb", _mod_adb)
import nas2gp.device.ui as _mod_ui        ; _register_alias("ui", _mod_ui)

# 3. Storage / DB
import nas2gp.storage.db as _mod_db                      ; _register_alias("db", _mod_db)
import nas2gp.storage.db_batch as _mod_db_batch           ; _register_alias("db_batch", _mod_db_batch)
import nas2gp.storage.db_stats as _mod_db_stats           ; _register_alias("db_stats", _mod_db_stats)
import nas2gp.storage.db_debug as _mod_db_debug           ; _register_alias("db_debug", _mod_db_debug)
import nas2gp.storage.db_maintenance as _mod_db_maint     ; _register_alias("db_maintenance", _mod_db_maint)
import nas2gp.storage.media as _mod_media                 ; _register_alias("media", _mod_media)

# 4. Scanner
import nas2gp.scanner.local as _mod_scanner  ; _register_alias("scanner", _mod_scanner)

# 5. Uploader
import nas2gp.uploader.flow as _mod_uploader  ; _register_alias("uploader", _mod_uploader)

# 6. Worker
import nas2gp.worker.sync_flow as _mod_sync          ; _register_alias("sync", _mod_sync)
import nas2gp.worker.backup_waiter as _mod_worker    ; _register_alias("worker", _mod_worker)

# 1. Core (settings / logger / util / i18n / config / events / lang)
import nas2gp.core.settings as _mod_settings      ; _register_alias("settings", _mod_settings)
import nas2gp.core.logger as _mod_logger          ; _register_alias("logger", _mod_logger)
import nas2gp.core.util as _mod_util              ; _register_alias("util", _mod_util)
import nas2gp.core.i18n as _mod_i18n              ; _register_alias("i18n", _mod_i18n)
import nas2gp.core.config as _mod_config          ; _register_alias("config", _mod_config)
import nas2gp.core.events as _mod_events          ; _register_alias("events", _mod_events)


# --------------------------------------------
# 1. Core re-export attributes (from nas2gp import X)
# --------------------------------------------
from nas2gp.core.settings import *  # noqa: F401,F403
from nas2gp.core.logger import Logger  # noqa: F401
from nas2gp.core.util import *  # noqa: F401,F403
from nas2gp.core.i18n import tr  # noqa: F401
from nas2gp.core.config import *  # noqa: F401,F403
from nas2gp.core import events  # noqa: F401

# --------------------------------------------
# 2. Device
# --------------------------------------------
from nas2gp.device.adb import ADB  # noqa: F401
from nas2gp.device.ui import *  # noqa: F401,F403
from nas2gp.device.device import Device  # noqa: F401

# __dict__ attribute 経由取得に対応 (from nas2gp import ui のため)
# Support attribute access via __dict__ (for `from nas2gp import ui`)
import nas2gp.device.ui as _ui_attr       ; globals()["ui"] = _ui_attr
import nas2gp.device.adb as _adb_attr     ; globals()["adb"] = _adb_attr

# --------------------------------------------
# 3. Storage / DB
# --------------------------------------------
from nas2gp.storage.db import Database  # noqa: F401
from nas2gp.storage.db_debug import DatabaseDebug  # noqa: F401
from nas2gp.storage.db_maintenance import DatabaseMaintenance  # noqa: F401
from nas2gp.storage import db_batch  # noqa: F401
from nas2gp.storage import db_stats  # noqa: F401
from nas2gp.storage.media import MediaFile  # noqa: F401

globals()["db"] = _mod_db
globals()["db_batch"] = _mod_db_batch
globals()["db_stats"] = _mod_db_stats
globals()["db_debug"] = _mod_db_debug
globals()["db_maintenance"] = _mod_db_maint
globals()["media"] = _mod_media

# --------------------------------------------
# 4. Scanner (local.py 旧scanner.py renamed)
# 4. Scanner (local.py renamed from the old scanner.py)
# --------------------------------------------
from nas2gp.scanner.local import Scanner  # noqa: F401
globals()["scanner"] = _mod_scanner

# --------------------------------------------
# 5. Uploader (flow.py 旧 uploader.py)
# 5. Uploader (flow.py renamed from the old uploader.py)
# --------------------------------------------
from nas2gp.uploader.flow import Uploader  # noqa: F401
globals()["uploader"] = _mod_uploader

# --------------------------------------------
# 6. Worker
# --------------------------------------------
from nas2gp.worker.sync_flow import Sync  # noqa: F401
from nas2gp.worker.backup_waiter import UploadWorker  # noqa: F401
globals()["sync"] = _mod_sync
globals()["worker"] = _mod_worker

# --------------------------------------------
# 1. Core globals (属性 from nas2gp import settings 等)
# 1. Core globals (attributes for `from nas2gp import settings`, etc.)
# --------------------------------------------
globals()["settings"] = _mod_settings
globals()["logger"] = _mod_logger
globals()["util"] = _mod_util
globals()["i18n"] = _mod_i18n
globals()["config"] = _mod_config
globals()["events"] = _mod_events

__all__ = [
    # Core
    "Logger", "tr",
    # Device
    "ADB", "Device",
    # Storage
    "Database", "DatabaseDebug", "DatabaseMaintenance", "MediaFile",
    # Scanner
    "Scanner",
    # Uploader / Worker
    "Uploader", "Sync", "UploadWorker",
]
