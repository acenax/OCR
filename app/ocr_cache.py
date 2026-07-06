"""OCR result cache for TMC OCR.

The cache avoids re-running Poppler/Tesseract when the same PDF is processed
again without any relevant changes. It is safe local runtime data and should not
be committed to Git.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
import hashlib
import json

from . import config
from .models import PODocument, POLine

CACHE_DIR = config.APP_DIR / ".ocr_cache"
CACHE_INDEX = CACHE_DIR / "index.json"
CACHE_VERSION = 1


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_float(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _file_stat(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    st = p.stat()
    return {
        "path": str(p.resolve()),
        "name": p.name,
        "size": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
    }


def _product_stat(matcher: Any) -> dict[str, Any]:
    loaded_from = getattr(matcher, "loaded_from", "") if matcher is not None else ""
    if not loaded_from:
        return {"path": "", "size": 0, "mtime_ns": 0}
    p = Path(str(loaded_from))
    if not p.exists():
        return {"path": str(p), "size": 0, "mtime_ns": 0}
    st = p.stat()
    return {"path": str(p.resolve()), "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}


def _cfg_fingerprint(cfg: Any, matcher: Any = None) -> dict[str, Any]:
    keys = ["dpi", "ocr_lang", "fuzzy_threshold", "fuzzy_strong", "poppler_path", "tesseract_path"]
    data = {}
    for key in keys:
        try:
            data[key] = cfg.get(key, "")
        except Exception:
            try:
                data[key] = cfg[key]
            except Exception:
                data[key] = ""
    data["product_details"] = _product_stat(matcher)
    data["cache_version"] = CACHE_VERSION
    return data


def _fingerprint(pdf_path: str | Path, customer: str, cfg: Any, matcher: Any = None) -> dict[str, Any]:
    return {"customer": str(customer), "file": _file_stat(pdf_path), "settings": _cfg_fingerprint(cfg, matcher)}


def _cache_id(meta: dict[str, Any]) -> str:
    raw = json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def ensure_cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def doc_to_dict(doc: PODocument) -> dict[str, Any]:
    return {
        "source_pdf": getattr(doc, "source_pdf", ""),
        "customer": getattr(doc, "customer", ""),
        "po_no": getattr(doc, "po_no", ""),
        "po_date": getattr(doc, "po_date", ""),
        "po_date_raw": getattr(doc, "po_date_raw", ""),
        "total": _safe_float(getattr(doc, "total", 0)),
        "vat": _safe_float(getattr(doc, "vat", 0)),
        "grand_total": _safe_float(getattr(doc, "grand_total", 0)),
        "warnings": list(getattr(doc, "warnings", []) or []),
        "lines": [asdict(line) if hasattr(line, "__dataclass_fields__") else dict(line) for line in getattr(doc, "lines", [])],
    }


def doc_from_dict(data: dict[str, Any]) -> PODocument:
    lines = []
    for raw in data.get("lines", []) or []:
        if not isinstance(raw, dict):
            continue
        allowed = getattr(POLine, "__dataclass_fields__", {}).keys()
        clean = {k: raw.get(k) for k in allowed if k in raw}
        try:
            lines.append(POLine(**clean))
        except Exception:
            line = POLine()
            for k, v in clean.items():
                try:
                    setattr(line, k, v)
                except Exception:
                    pass
            lines.append(line)
    allowed_doc = getattr(PODocument, "__dataclass_fields__", {}).keys()
    clean_doc = {k: data.get(k) for k in allowed_doc if k in data and k != "lines"}
    doc = PODocument(**clean_doc)
    doc.lines = lines
    doc.warnings = list(data.get("warnings", []) or [])
    return doc


def _index_load() -> dict[str, Any]:
    if not CACHE_INDEX.exists():
        return {"created_at": _now(), "items": {}}
    try:
        data = json.loads(CACHE_INDEX.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"created_at": _now(), "items": {}}
        data.setdefault("items", {})
        return data
    except Exception:
        return {"created_at": _now(), "items": {}}


def _index_save(data: dict[str, Any]) -> None:
    ensure_cache_dir()
    data["updated_at"] = _now()
    tmp = CACHE_INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CACHE_INDEX)


def cache_path_for(pdf_path: str | Path, customer: str, cfg: Any, matcher: Any = None) -> tuple[Path, dict[str, Any], str]:
    meta = _fingerprint(pdf_path, customer, cfg, matcher)
    cid = _cache_id(meta)
    return ensure_cache_dir() / f"{cid}.json", meta, cid


def load_cached_document(pdf_path: str | Path, customer: str, cfg: Any, matcher: Any = None) -> PODocument | None:
    try:
        p, meta, cid = cache_path_for(pdf_path, customer, cfg, matcher)
    except Exception:
        return None
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("meta") != meta:
            return None
        doc = doc_from_dict(data.get("document", {}))
        setattr(doc, "cache_hit", True)
        setattr(doc, "cache_id", cid)
        return doc
    except Exception:
        return None


def save_document(pdf_path: str | Path, customer: str, cfg: Any, matcher: Any, doc: PODocument) -> Path:
    p, meta, cid = cache_path_for(pdf_path, customer, cfg, matcher)
    payload = {"cache_version": CACHE_VERSION, "created_at": _now(), "meta": meta, "document": doc_to_dict(doc)}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    idx = _index_load()
    idx.setdefault("items", {})[cid] = {
        "cache_id": cid,
        "created_at": payload["created_at"],
        "customer": str(customer),
        "pdf_path": meta["file"]["path"],
        "pdf_name": meta["file"]["name"],
        "size": meta["file"]["size"],
        "mtime_ns": meta["file"]["mtime_ns"],
        "cache_file": str(p),
        "item_count": int(len(getattr(doc, "lines", []) or [])),
    }
    _index_save(idx)
    return p


def delete_cache_for_file(pdf_path: str | Path, customer: str | None = None) -> int:
    idx = _index_load()
    items = idx.get("items", {}) if isinstance(idx, dict) else {}
    target = str(Path(pdf_path).resolve())
    removed = 0
    for cid, info in list(items.items()):
        if info.get("pdf_path") == target and (customer is None or info.get("customer") == customer):
            try:
                cf = Path(info.get("cache_file", ""))
                if cf.exists():
                    cf.unlink()
            except Exception:
                pass
            items.pop(cid, None)
            removed += 1
    idx["items"] = items
    _index_save(idx)
    return removed


def clear_all_cache() -> int:
    if not CACHE_DIR.exists():
        return 0
    count = 0
    for p in CACHE_DIR.glob("*.json"):
        try:
            p.unlink()
            count += 1
        except Exception:
            pass
    return count


def stats() -> dict[str, Any]:
    idx = _index_load()
    items = idx.get("items", {}) if isinstance(idx, dict) else {}
    total_size = 0
    for info in items.values():
        try:
            cf = Path(info.get("cache_file", ""))
            if cf.exists():
                total_size += cf.stat().st_size
        except Exception:
            pass
    return {"count": len(items), "size_bytes": total_size, "dir": str(CACHE_DIR)}


def list_items() -> list[dict[str, Any]]:
    idx = _index_load()
    items = idx.get("items", {}) if isinstance(idx, dict) else {}
    return sorted(items.values(), key=lambda x: x.get("created_at", ""), reverse=True)
