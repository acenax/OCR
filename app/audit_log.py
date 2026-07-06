"""Simple JSONL audit log for important OCR actions."""
from __future__ import annotations

from datetime import datetime
from typing import Any
import json

from . import config

LOG_DIR = config.APP_DIR / "logs"
LOG_FILE = LOG_DIR / "audit.jsonl"


def log_event(event: str, **data: Any) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "event": str(event)}
        rec.update(data)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def tail(limit: int = 200) -> list[dict[str, Any]]:
    if not LOG_FILE.exists():
        return []
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
        return out
    except Exception:
        return []
