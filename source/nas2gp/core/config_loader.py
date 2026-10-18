from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


_CONFIG_DIR_NAME = "config"
_CONFIG_FILE_NAME = "config_ui.json"
_CONFIG_SCHEMA_VERSION = 10


_DEFAULT_CONFIG: Dict[str, Any] = {
    "CONFIG_SCHEMA_VERSION": _CONFIG_SCHEMA_VERSION,

    "PHOTO_DIR": "D:/Photos",

    # v1.8.0: 端末 側 の 作業フォルダ (転送先 兼 削除 対象)。DCIM 配下 のみ 許可。
    # v1.8.0: device-side working folder (transfer + delete target); DCIM-only.
    "DEVICE_WORK_DIR": "/sdcard/DCIM/NAS2GP",

    "BATCH_STRATEGY": "hybrid",
    "MAX_FILES_PER_BATCH": 30,
    "MAX_BYTES_PER_BATCH": 2 * 1024 ** 3,

    "DEVICE_STORAGE_MARGIN_GB": 4.0,

    "ADB_TIMEOUT": 60,
    "ADB_PUSH_RETRY": 3,
    "ADB_RECOVER_MIN_INTERVAL_SEC": 45.0,
    "ADB_FAILURE_POLICY": "pause_dialog",

    "ADB_RECOVERY_WAIT_SEC": 300,
    "BATTERY_LOW_THRESHOLD_PCT": 20,
    "BATTERY_WAIT_MAX_SEC": 3600,
    "BATTERY_CHECK_INTERVAL_SEC": 600,

    "DISCONNECT_LOG_ENABLED": True,
    "DISCONNECT_HEARTBEAT_INTERVAL_SEC": 600,

    "BACKUP_TIMEOUT": 600,
    "BACKUP_ABSOLUTE_MAX_SECONDS": 7200,
    "MIN_BACKUP_THROUGHPUT_MBPS": 2.0,
    "BACKUP_CHECK_INTERVAL": 8,
    "MIN_ACCEPT_ELAPSED_SECONDS": 15,
    "BACKUP_STALLED_LIMIT": 15,

    "RESTART_PHOTOS_AFTER_PUSH": False,
    "CYCLE_GP_BEFORE_WAIT": True,
    "LOG_VERBOSITY": "standard",

    "ENABLE_NIGHT_LIGHT_MODE": True,
    "SCREEN_BRIGHTNESS_NIGHT": 10,
    "SCREEN_BRIGHTNESS_DEFAULT": 150,

    "LANGUAGE": "auto",

    "WEB_SERVER_HOST": "0.0.0.0",
    "WEB_SERVER_PORT": 8000,
}


_CONFIG_REQUIRED_KEYS = tuple(sorted(_DEFAULT_CONFIG.keys()))


def _get_config_path() -> Path:
    return Path.cwd() / _CONFIG_DIR_NAME / _CONFIG_FILE_NAME


def _merge_missing_schema(loaded: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(loaded or {})
    # DB_FILE は settings.DB_FILE を 唯一 の 正 と する ため config から 除外 する
    # (旧 config に 残る 誤った DB_FILE を 読み捨て、次回 保存 で 除去)。
    # DB_FILE is excluded from config since settings.DB_FILE is the single source
    # of truth (discard a stale DB_FILE in old configs; next save drops it).
    merged.pop("DB_FILE", None)
    for k, v in _DEFAULT_CONFIG.items():
        if k not in merged:
            merged[k] = v
    merged["CONFIG_SCHEMA_VERSION"] = _CONFIG_SCHEMA_VERSION
    return merged


def _apply_runtime_settings(cfg: Dict[str, Any], silent: bool = False) -> None:
    from nas2gp.core import settings
    from nas2gp.core.i18n import reload_language

    cfg = dict(cfg or {})

    def _log(msg: str) -> None:
        if not silent:
            try:
                from nas2gp.core.logger import Logger
                Logger.debug(msg)
            except Exception:
                pass

    _ints = [
        "MAX_FILES_PER_BATCH", "MAX_BYTES_PER_BATCH",
        "ADB_TIMEOUT", "ADB_PUSH_RETRY",
        "BACKUP_TIMEOUT", "BACKUP_ABSOLUTE_MAX_SECONDS", "BACKUP_CHECK_INTERVAL",
        "MIN_ACCEPT_ELAPSED_SECONDS", "BACKUP_STALLED_LIMIT",
        "SCREEN_BRIGHTNESS_NIGHT", "SCREEN_BRIGHTNESS_DEFAULT",
        "ADB_RECOVERY_WAIT_SEC", "BATTERY_LOW_THRESHOLD_PCT",
        "BATTERY_WAIT_MAX_SEC", "BATTERY_CHECK_INTERVAL_SEC",
        "DISCONNECT_HEARTBEAT_INTERVAL_SEC",
        "WEB_SERVER_PORT",
    ]
    for k in _ints:
        if k in cfg:
            try:
                v = int(cfg[k])
                if k == "WEB_SERVER_PORT":
                    if v < 1:
                        v = 1
                    elif v > 65535:
                        v = 65535
                setattr(settings, k, v)
                _log(f"[config] apply INT {k} = {v}")
            except Exception:
                pass

    _floats = ["DEVICE_STORAGE_MARGIN_GB", "ADB_RECOVER_MIN_INTERVAL_SEC", "MIN_BACKUP_THROUGHPUT_MBPS"]
    for k in _floats:
        if k in cfg:
            try:
                v = float(cfg[k])
                if v < 0.0:
                    v = 0.0
                setattr(settings, k, v)
                _log(f"[config] apply FLOAT {k} = {v:.2f}")
            except Exception:
                pass

    _strs = ["BATCH_STRATEGY", "ADB_FAILURE_POLICY", "LOG_VERBOSITY", "LANGUAGE", "WEB_SERVER_HOST"]
    for k in _strs:
        if k in cfg:
            try:
                v_raw = str(cfg[k]).strip()
                v = v_raw.lower()
                if k == "BATCH_STRATEGY":
                    if v not in ("count", "bytes", "hybrid"):
                        v = "count"
                elif k == "ADB_FAILURE_POLICY":
                    if v not in ("pause_dialog", "continue"):
                        v = "pause_dialog"
                elif k == "LOG_VERBOSITY":
                    if v not in ("full", "standard", "minimal"):
                        v = "standard"
                elif k == "WEB_SERVER_HOST":
                    v = v_raw
                else:
                    v = v_raw
                setattr(settings, k, v)
                _log(f"[config] apply STR {k} = {v!r}")
            except Exception:
                pass

    _bools = ["RESTART_PHOTOS_AFTER_PUSH", "ENABLE_NIGHT_LIGHT_MODE", "CYCLE_GP_BEFORE_WAIT",
              "DISCONNECT_LOG_ENABLED"]
    for k in _bools:
        if k in cfg:
            try:
                v = bool(cfg[k])
                setattr(settings, k, v)
                _log(f"[config] apply BOOL {k} = {v}")
            except Exception:
                pass

    # DB_FILE は config 対象 外 (settings.resolve_db_file() が 唯一 の 正)。
    # DB_FILE is not configurable (settings.resolve_db_file() is the single truth).
    if "PHOTO_DIR" in cfg and cfg["PHOTO_DIR"]:
        try:
            raw = str(cfg["PHOTO_DIR"])
            setattr(settings, "PHOTO_DIR", Path(raw))
            _log(f"[config] apply PATH PHOTO_DIR = {raw!r}")
        except Exception:
            pass

    # v1.8.0: 端末 側 の 作業フォルダ 。 DCIM 配下 のみ 受理 する
    #   (Google Photos の 自動 バックアップ 対象 で あ る こと を 担保 する ため)。
    # v1.8.0: device-side working folder. Only paths under DCIM are accepted so it
    #   stays a Google Photos auto-backup target.
    if "DEVICE_WORK_DIR" in cfg and cfg["DEVICE_WORK_DIR"]:
        try:
            raw = str(cfg["DEVICE_WORK_DIR"]).strip().rstrip("/")
            if raw.startswith("/sdcard/DCIM/"):
                setattr(settings, "DEVICE_WORK_DIR", raw)
                _log(f"[config] apply WORK_DIR DEVICE_WORK_DIR = {raw!r}")
            else:
                _log(f"[config] ignore DEVICE_WORK_DIR (outside DCIM) = {raw!r}")
        except Exception:
            pass

    # v1.7.5 NEW: LOG_VERBOSITY (allowed: full / standard / minimal)
    if "LOG_VERBOSITY" in cfg and cfg["LOG_VERBOSITY"]:
        try:
            allowed = getattr(settings, "LOG_VERBOSITY_ALLOWED", ("full", "standard", "minimal"))
            v = str(cfg["LOG_VERBOSITY"]).strip().lower()
            if v not in allowed:
                v = "standard"
            setattr(settings, "LOG_VERBOSITY", v)
            _log(f"[config] apply LOG_VERBOSITY = {v!r}")
        except Exception:
            pass

    if "LANGUAGE" in cfg and cfg["LANGUAGE"]:
        try:
            lang = str(cfg["LANGUAGE"]).strip().lower()
            if lang and lang != "auto":
                reload_language(lang)
                _log(f"[config] apply LANGUAGE = {lang!r}")
        except Exception:
            pass


def load_and_apply_gui_config(silent: bool = False) -> Dict[str, Any]:
    """Web UI / CLI で 保存 し た config/config_ui.json を 読み込ん で settings へ 適用 する 共通 関数。
    Web UI (ui_web) と CLI (app.py) の 両方 から 呼べる 共通 実装。

    Args:
        silent: True なら 読み込み 成功 時 の 詳細 ログ を Logger.debug 限定 に 抑える (CLI 向け)

    Returns:
        最終的 に settings へ 適用 し た 設定 dict (merged + schema補完 済み)

    Shared helper that loads config/config_ui.json saved by the Web UI / CLI and applies it to settings.
    Common implementation callable from both the Web UI (ui_web) and CLI (app.py).
    """
    cfg_path = _get_config_path()
    loaded_cfg: Optional[Dict[str, Any]] = None
    if cfg_path.exists() and cfg_path.is_file():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                loaded_cfg = json.load(f)
            if not isinstance(loaded_cfg, dict):
                loaded_cfg = None
        except Exception:
            loaded_cfg = None
    merged = _merge_missing_schema(loaded_cfg)
    _apply_runtime_settings(merged, silent=silent)
    return merged


def save_gui_config(cfg: Dict[str, Any], silent: bool = False) -> bool:
    """GUI 側 から settings 値 を config_ui.json へ 保存 する 共通 関数。
    中身 は schema v4 強制 + 親 ディレクトリ 自動 作成。

    Shared helper that saves settings values from the GUI into config_ui.json.
    Internally enforces schema v4 and auto-creates the parent directory.
    """
    cfg_path = _get_config_path()
    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        save_dict = dict(cfg or {})
        save_dict["CONFIG_SCHEMA_VERSION"] = _CONFIG_SCHEMA_VERSION
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(save_dict, f, ensure_ascii=False, indent=2)
        if not silent:
            try:
                from nas2gp.core.logger import Logger
                Logger.success(f"[config] saved → {cfg_path}")
            except Exception:
                pass
        return True
    except Exception as e:
        if not silent:
            try:
                from nas2gp.core.logger import Logger
                Logger.error(f"[config] save FAILED → {cfg_path}: {e}")
            except Exception:
                pass
        return False


def get_default_config() -> Dict[str, Any]:
    """UI 初期値 / Reset 時 に 使う デフォルト 設定 を 返す 共通 関数。

    Shared helper returning the default config used for UI initial values / Reset.
    """
    return dict(_DEFAULT_CONFIG)


def get_required_keys() -> tuple:
    """診断 タブ / Schema 検証 用 の 必須 キー 一覧 を 返す。

    Return the list of required keys for the diagnostics tab / schema validation.
    """
    return _CONFIG_REQUIRED_KEYS


def get_config_schema_version() -> int:
    return _CONFIG_SCHEMA_VERSION
