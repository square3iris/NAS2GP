# ============================================
# FILE: nas2gp/media.py
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