# ============================================
# FILE: pixel_sync/media.py
# VERSION: 1.1.0
# UPDATED: 2026-06-18
# ============================================
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MediaFile:
    path: Path
    filename: str
    extension: str
    size: int
    mtime: float
    media_type: str