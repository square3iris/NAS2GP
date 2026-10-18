# ============================================
# FILE: pixel_sync/logger.py
# VERSION: 1.3.0 (Web capture only / GUI hook removed)
# UPDATED: 2026-10-17
# ============================================

from datetime import datetime
from pathlib import Path


class Logger:

    try:
        from pixel_sync.core.settings import WORK_ROOT as _WORK_ROOT
        LOG_DIR = (_WORK_ROOT / "logs").resolve()
    except Exception:
        LOG_DIR = Path("logs").resolve()
    _WEB_CAPTURE_FN = None

    _LEVEL_STDOUT = frozenset(["INFO", "SUCCESS", "WARNING", "ERROR", "PRINT"])
    _LEVEL_CAPTURE = frozenset(["INFO", "SUCCESS", "WARNING", "ERROR", "DEBUG", "PRINT"])

    @classmethod
    def _install_web_capture(cls, fn):
        cls._WEB_CAPTURE_FN = staticmethod(fn) if fn else None
        return True

    @classmethod
    def _write(cls, level, message):

        now = datetime.now()

        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        line = f"[{timestamp}] {level:<7} {message}"

        if level in cls._LEVEL_STDOUT:
            try:
                print(line, flush=True)
            except Exception:
                try:
                    sys_stdout = __import__("sys").stdout
                    try:
                        sys_stdout.write(line + "\n")
                        sys_stdout.flush()
                    except Exception:
                        pass
                except Exception:
                    pass

        cls.LOG_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        logfile = (
            cls.LOG_DIR
            / f"{now:%Y-%m-%d}.log"
        )

        try:
            with open(
                logfile,
                "a",
                encoding="utf-8",
            ) as f:

                f.write(line + "\n")
        except Exception:
            pass

        if cls._WEB_CAPTURE_FN and level in cls._LEVEL_CAPTURE:
            try:
                cls._WEB_CAPTURE_FN(level=level, message=message, ts=timestamp)
            except Exception:
                pass

    @classmethod
    def debug(cls, message):

        cls._write("DEBUG", message)

    @classmethod
    def info(cls, message):

        cls._write("INFO", message)

    @classmethod
    def success(cls, message):

        cls._write("SUCCESS", message)

    @classmethod
    def warning(cls, message):

        cls._write("WARNING", message)

    @classmethod
    def error(cls, message):

        cls._write("ERROR", message)
