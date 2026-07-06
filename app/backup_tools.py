"""Simple local backup/restore helpers for the OCR SQLite database."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil

from . import config

BACKUP_DIR = config.APP_DIR / "backups"


def ensure_backup_dir() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


def backup_database(label: str = "manual") -> Path:
    src = config.DB_PATH
    if not src.exists():
        raise FileNotFoundError(f"ไม่พบฐานข้อมูล: {src}")
    dest_dir = ensure_backup_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(ch for ch in label if ch.isalnum() or ch in ("-", "_")) or "manual"
    dest = dest_dir / f"tmc_ocr_{safe_label}_{ts}.db"
    shutil.copy2(src, dest)
    return dest


def list_backups() -> list[Path]:
    if not BACKUP_DIR.exists():
        return []
    return sorted(BACKUP_DIR.glob("tmc_ocr_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)


def restore_database(backup_file: str | Path) -> Path:
    src = Path(backup_file)
    if not src.exists():
        raise FileNotFoundError(f"ไม่พบไฟล์ backup: {src}")
    # Always make a safety copy before replacing the active DB.
    safety = None
    if config.DB_PATH.exists():
        safety = backup_database("before_restore")
    shutil.copy2(src, config.DB_PATH)
    return safety if safety else config.DB_PATH
