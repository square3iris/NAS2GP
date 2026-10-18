# ============================================
# FILE: pixel_sync/core/i18n.py
# VERSION: 3.0.0 (Phase i18n v3: JSON 外部言語 + 動的スキャン)
# VERSION: 3.0.0 (Phase i18n v3: external JSON languages + dynamic scan)
# UPDATED: 2026-08-08
# ============================================
import os as _os
import sys as _sys
import json as _json
from pathlib import Path as _Path


def _detect_os_locale() -> str:
    for _env_key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        _v = _os.environ.get(_env_key)
        if _v and _v.strip() and _v.strip().upper() not in ("C", "POSIX"):
            return _v.strip()
    try:
        import locale as _locale
        _code = None
        try:
            _code = _locale.getlocale()[0]
        except Exception:
            _code = None
        if not _code:
            try:
                _code = _locale.getdefaultlocale()[0]
            except Exception:
                _code = None
        if _code:
            return _code
    except Exception:
        pass
    try:
        import ctypes as _ctypes
        _dll = _ctypes.windll.kernel32
        _LOCALE_NAME_MAX_LENGTH = 85
        _buf = _ctypes.create_unicode_buffer(_LOCALE_NAME_MAX_LENGTH)
        _ret = _dll.GetUserDefaultLocaleName(_buf, _LOCALE_NAME_MAX_LENGTH)
        if _ret > 0 and _buf.value:
            return _buf.value
    except Exception:
        pass
    return ""


def _resolve_language(raw: str) -> str:
    r = (raw or "").strip().lower()
    if r in ("ja", "jp", "jpn", "ja_jp", "ja-jp", "japanese", "nihongo"):
        return "ja"
    if r in ("en", "eng", "en_us", "en-us", "en_gb", "en-gb", "english", "eigo"):
        return "en"
    external = _list_external_language_codes()
    if r in external:
        return r
    raw_locale = _detect_os_locale() or ""
    loc = raw_locale.lower().replace("-", "_")
    if loc.startswith("ja") or "_ja" in loc or "jpn" in loc:
        return "ja"
    if loc.startswith("en") or "_en" in loc or "eng" in loc:
        return "en"
    if raw == "auto" or r == "auto":
        return "en"
    return r or "en"


_EXTERNAL_LANG_DIR = _Path("config/lang")


def _set_external_lang_dir(path=None):
    global _EXTERNAL_LANG_DIR
    if path is None:
        _EXTERNAL_LANG_DIR = _Path("config/lang")
    else:
        _EXTERNAL_LANG_DIR = _Path(path)


def _list_external_language_codes():
    codes = set()
    try:
        d = _EXTERNAL_LANG_DIR
        if d.exists() and d.is_dir():
            for f in sorted(d.glob("*.json")):
                codes.add(f.stem.lower())
    except Exception:
        pass
    return sorted(codes)


def list_available_languages() -> list:
    base = [("auto", "settings.lang.auto"), ("ja", "settings.lang.ja"), ("en", "settings.lang.en")]
    external_codes = _list_external_language_codes()
    seen = {"auto", "ja", "en"}
    for code in external_codes:
        if code in seen:
            continue
        seen.add(code)
        try:
            meta = _load_external_metadata(code) or {}
            label = meta.get("label") or meta.get("name") or f"External: {code}"
            base.append((code, f"external:{label}"))
        except Exception:
            base.append((code, f"external:{code}"))
    return base


def _load_external_metadata(code: str):
    try:
        p = _EXTERNAL_LANG_DIR / f"{code}.json"
        if not p.exists():
            return None
        with open(p, "r", encoding="utf-8") as f:
            data = _json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _load_word_bank_py(eff_lang: str):
    if eff_lang == "ja":
        from pixel_sync.core.lang.ja import WORDS as _w
        return dict(_w)
    if eff_lang == "en":
        from pixel_sync.core.lang.en import WORDS as _w
        return dict(_w)
    return None


def _load_external_word_bank(code: str):
    meta = _load_external_metadata(code)
    if not meta:
        return None
    words = None
    if "words" in meta and isinstance(meta["words"], dict):
        words = dict(meta["words"])
    try:
        p = _EXTERNAL_LANG_DIR / f"{code}.json"
        if p.exists():
            mtime = int(p.stat().st_mtime)
            if isinstance(words, dict):
                words["__meta__"] = {
                    "path": str(p),
                    "mtime_unix": mtime,
                    "label": meta.get("label") or meta.get("name") or code,
                }
    except Exception:
        pass
    return words


def _load_word_bank(eff_lang: str):
    base = _load_word_bank_py("ja") or {}
    if eff_lang == "ja":
        return dict(base)
    en = _load_word_bank_py("en") or {}
    if eff_lang == "en":
        out = dict(base)
        out.update(en)
        return out
    ext = _load_external_word_bank(eff_lang)
    if not ext:
        out = dict(base)
        out.update(en)
        return out
    out = dict(base)
    out.update(en)
    for k, v in ext.items():
        if k.startswith("__"):
            continue
        if isinstance(v, str):
            out[k] = v
    out.setdefault("__meta__", {})
    meta = ext.get("__meta__") or {}
    if isinstance(out.get("__meta__"), dict) and isinstance(meta, dict):
        out["__meta__"].update(meta)
    out["__meta__"].setdefault("code", eff_lang)
    return out


def diagnose_wordbank(eff_lang: str) -> dict:
    base_ja = _load_word_bank_py("ja") or {}
    base_en = _load_word_bank_py("en") or {}
    target = _load_word_bank(eff_lang)
    target_keys = set(k for k in target.keys() if not k.startswith("__"))
    base_keys_ja = set(base_ja.keys())
    base_keys_en = set(base_en.keys())
    canonical = base_keys_ja | base_keys_en
    missing = sorted(canonical - target_keys)
    duplicates_ja = len(base_ja) - len(set(base_ja))
    duplicates_en = len(base_en) - len(set(base_en))
    meta = target.get("__meta__") if isinstance(target.get("__meta__"), dict) else {}
    return {
        "code": eff_lang,
        "total_canonical": len(canonical),
        "total_in_bank": len(target_keys),
        "missing_count": len(missing),
        "missing_samples": missing[:10],
        "duplicates_ja": duplicates_ja,
        "duplicates_en": duplicates_en,
        "ja_en_match": base_keys_ja == base_keys_en,
        "ja_keys": len(base_keys_ja),
        "en_keys": len(base_keys_en),
        "external_path": meta.get("path"),
        "external_mtime_unix": meta.get("mtime_unix"),
        "external_label": meta.get("label"),
    }


def _apply_language(setting_value: str) -> tuple:
    eff = _resolve_language(setting_value)
    words = _load_word_bank(eff)
    msg = None
    raw = (setting_value or "").strip().lower()
    if raw in ("auto", "detect", "system"):
        try:
            raw_loc = _detect_os_locale() or "(unknown)"
            k = "i18n.auto.detected"
            if k in words:
                msg = words[k].format(lc=raw_loc, lang=eff)
            else:
                msg = f"[i18n auto] OS locale = {raw_loc} -> LANGUAGE={eff}"
        except Exception:
            msg = None
    return eff, words, msg


_LANGUAGE_EFFECTIVE = "ja"
_WORDS = {}
_INITIALIZED = False


def _ensure_initialized():
    global _INITIALIZED, _LANGUAGE_EFFECTIVE, _WORDS
    if _INITIALIZED:
        return
    from pixel_sync.core.settings import LANGUAGE as _RAW
    eff, wb, msg = _apply_language(_RAW)
    _LANGUAGE_EFFECTIVE = eff
    _WORDS = wb
    _INITIALIZED = True
    if msg:
        try:
            print(msg)
        except Exception:
            pass


def reload_language(setting_value=None):
    global _INITIALIZED, _LANGUAGE_EFFECTIVE, _WORDS
    if setting_value is None:
        from pixel_sync.core.settings import LANGUAGE as _RAW
        setting_value = _RAW
    eff, wb, msg = _apply_language(setting_value)
    _LANGUAGE_EFFECTIVE = eff
    _WORDS = wb
    _INITIALIZED = True
    return eff, wb


def tr(key):
    _ensure_initialized()
    return _WORDS.get(key, key)


def __getattr__(name):
    if name == "LANGUAGE_EFFECTIVE":
        _ensure_initialized()
        return _LANGUAGE_EFFECTIVE
    if name == "WORDS":
        _ensure_initialized()
        return _WORDS
    raise AttributeError(name)


if not hasattr(_sys.modules[__name__], "LANGUAGE_EFFECTIVE"):
    LANGUAGE_EFFECTIVE = _LANGUAGE_EFFECTIVE
    WORDS = _WORDS
