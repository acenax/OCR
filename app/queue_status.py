"""Local OCR queue/status store."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import json

from . import config

STATUS_PATH = config.APP_DIR / "ocr_queue_status.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _key(path: str | Path) -> str:
    try:
        return str(Path(path).resolve())
    except Exception:
        return str(path)


def _load() -> dict[str, Any]:
    if not STATUS_PATH.exists():
        return {"created_at": _now(), "items": {}}
    try:
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"created_at": _now(), "items": {}}
        data.setdefault("items", {})
        return data
    except Exception:
        return {"created_at": _now(), "items": {}}


def _save(data: dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATUS_PATH)


def set_status(path: str | Path, status: str, *, customer: str = "", message: str = "", item_count: int = 0, warnings: list[str] | None = None, cache_hit: bool = False) -> None:
    data = _load()
    items = data.setdefault("items", {})
    k = _key(path)
    p = Path(str(path))
    old = items.get(k, {})
    rec = dict(old) if isinstance(old, dict) else {}
    rec.update({
        "path": k,
        "name": p.name,
        "customer": customer or rec.get("customer", ""),
        "status": status,
        "message": message,
        "item_count": int(item_count or 0),
        "warnings": list(warnings or []),
        "cache_hit": bool(cache_hit),
        "updated_at": _now(),
    })
    try:
        if p.exists():
            st = p.stat()
            rec["size"] = int(st.st_size)
            rec["mtime_ns"] = int(st.st_mtime_ns)
    except Exception:
        pass
    items[k] = rec
    _save(data)


def list_statuses() -> list[dict[str, Any]]:
    data = _load()
    items = data.get("items", {}) if isinstance(data, dict) else {}
    return sorted(items.values(), key=lambda x: x.get("updated_at", ""), reverse=True)


def clear(statuses: set[str] | None = None) -> int:
    data = _load()
    items = data.get("items", {}) if isinstance(data, dict) else {}
    removed = 0
    for k, rec in list(items.items()):
        if statuses is None or rec.get("status") in statuses:
            items.pop(k, None)
            removed += 1
    data["items"] = items
    _save(data)
    return removed


def stats() -> dict[str, int]:
    out: dict[str, int] = {}
    for rec in list_statuses():
        st = str(rec.get("status", "unknown"))
        out[st] = out.get(st, 0) + 1
    return out
