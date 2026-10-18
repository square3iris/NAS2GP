# ============================================
# FILE: nas2gp/core/consent.py
# ============================================

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


_CONFIG_DIR_NAME = "config"
_CONSENT_FILE_NAME = "consent.json"


def get_consent_path() -> Path:
    """同意 記録 ファイル の パス を 返す (config_loader と 同じ config/ 基準)。

    Returns the path of the consent record (same config/ base as config_loader)."""
    return Path.cwd() / _CONFIG_DIR_NAME / _CONSENT_FILE_NAME


def load_consent() -> Dict[str, Any]:
    """保存 済み の 同意 記録 を 返す 。 まだ 無い / 読め ない 場合 は {} 。

    Returns the stored consent record; {} when missing or unreadable."""
    p = get_consent_path()
    try:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def is_accepted() -> bool:
    """同意 済み か どう か 。 True なら 同意 の 確認 画面 を 出さ ない 。

    Whether the user has accepted; True hides the consent gate."""
    return bool(load_consent().get("accepted"))


def accept(version: str = "") -> Dict[str, Any]:
    """同意 を 記録 する (同意 日時 と その 時 の バージョン を 保存)。

    Records the acceptance (timestamp and the version at that time)."""
    data = {
        "accepted": True,
        "accepted_at": datetime.now().isoformat(timespec="seconds"),
        "version": str(version or ""),
    }
    p = get_consent_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def reset() -> bool:
    """同意 記録 を 削除 する (次回 アクセス で 確認 画面 が 再表示 さ れる)。
    消す の は consent.json 1 ファイル だけ 。

    Removes the consent record so the gate reappears on the next access.
    Only the single consent.json file is deleted."""
    p = get_consent_path()
    try:
        if p.exists():
            p.unlink()
        return True
    except Exception:
        return False
