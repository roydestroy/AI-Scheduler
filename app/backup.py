"""
Backups — zip the whole data directory so a dead disk or a bad edit is
never fatal. A snapshot is taken automatically before risky operations
and can be downloaded on demand from the UI.
"""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

from .store import _DATA_DIR

_BACKUP_DIR = _DATA_DIR / "backups"
KEEP = 20


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in _DATA_DIR.rglob("*.json"):
            if _BACKUP_DIR in p.parents:
                continue
            z.write(p, p.relative_to(_DATA_DIR))
    return buf.getvalue()


def make_backup(tag: str = "") -> Path:
    """Write a timestamped zip of data/, prune old ones, return its path."""
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = "".join(ch for ch in tag if ch.isalnum() or ch in "-_")[:30]
    name = f"backup-{stamp}{('-' + safe) if safe else ''}.zip"
    path = _BACKUP_DIR / name
    path.write_bytes(_zip_bytes())

    backups = sorted(_BACKUP_DIR.glob("backup-*.zip"))
    for old in backups[:-KEEP]:
        old.unlink()
    return path


def download_bytes() -> tuple[str, bytes]:
    """Fresh zip for immediate download (not stored)."""
    return (f"scheduler-backup-{time.strftime('%Y%m%d-%H%M%S')}.zip", _zip_bytes())


def list_backups() -> list[dict]:
    if not _BACKUP_DIR.exists():
        return []
    out = []
    for p in sorted(_BACKUP_DIR.glob("backup-*.zip"), reverse=True):
        out.append({"name": p.name, "size": p.stat().st_size,
                    "when": time.strftime("%Y-%m-%d %H:%M",
                                          time.localtime(p.stat().st_mtime))})
    return out
