"""Local mapping memory for manual OCR corrections.

Purpose:
- When the user corrects/chooses a tmc_code for a noisy OCR product text,
  remember that pair per customer.
- On the next OCR run, reuse the remembered mapping before asking the user
  to correct the same noisy text again.

Data is stored locally in mapping_memory.json next to settings.json/db.
No external service is used.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from . import config
from .matcher import normalize
from .models import PODocument, POLine

MEMORY_PATH = config.APP_DIR / "mapping_memory.json"


@dataclass
class MemoryHit:
    tmc_code: str
    matched_name: str = ""
    stock_group_code: str = ""
    source_text: str = ""
    updated_at: str = ""
    used_count: int = 0


def _load_all() -> dict[str, dict[str, dict[str, Any]]]:
    if not MEMORY_PATH.exists():
        return {}
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_all(data: dict[str, dict[str, dict[str, Any]]]) -> None:
    MEMORY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _source_text(line: POLine) -> str:
    # DESCRIPTION is usually the real part/product name. If it is empty,
    # fall back to customer product code and finally both joined.
    desc = str(getattr(line, "description_raw", "") or "").strip()
    code = str(getattr(line, "product_code_raw", "") or "").strip()
    if len(normalize(desc)) >= 3:
        return desc
    if len(normalize(code)) >= 3:
        return code
    return (code + " " + desc).strip()


def key_for_line(line: POLine) -> str:
    return normalize(_source_text(line))


def lookup(customer: str, line: POLine) -> MemoryHit | None:
    key = key_for_line(line)
    if not customer or len(key) < 3:
        return None
    data = _load_all()
    rec = data.get(customer, {}).get(key)
    if not rec or not rec.get("tmc_code"):
        return None
    try:
        return MemoryHit(**{k: rec.get(k, "") for k in MemoryHit.__dataclass_fields__.keys()})
    except Exception:
        return MemoryHit(tmc_code=str(rec.get("tmc_code", "")))


def remember_line(customer: str, line: POLine) -> bool:
    key = key_for_line(line)
    tmc_code = str(getattr(line, "tmc_code", "") or "").strip()
    if not customer or len(key) < 3 or not tmc_code:
        return False

    data = _load_all()
    cust_map = data.setdefault(customer, {})
    old = cust_map.get(key, {})
    used_count = int(old.get("used_count", 0) or 0)
    hit = MemoryHit(
        tmc_code=tmc_code,
        matched_name=str(getattr(line, "matched_name", "") or "").strip(),
        stock_group_code=str(getattr(line, "stock_group_code", "") or "").strip(),
        source_text=_source_text(line),
        updated_at=datetime.now().isoformat(timespec="seconds"),
        used_count=used_count,
    )
    cust_map[key] = asdict(hit)
    _save_all(data)
    return True


def remember_document(customer: str, doc: PODocument) -> int:
    count = 0
    for line in getattr(doc, "lines", []) or []:
        if remember_line(customer or getattr(doc, "customer", ""), line):
            count += 1
    return count


def apply_to_document(customer: str, doc: PODocument, *, override_fuzzy: bool = True) -> int:
    """Apply remembered mapping to a newly OCRed document.

    We only override lines that are currently no_match/empty, and optionally fuzzy.
    Strong exact matches from Product Details are left untouched.
    """
    data = _load_all()
    cust_map = data.get(customer or getattr(doc, "customer", ""), {})
    if not cust_map:
        return 0

    applied = 0
    changed_memory = False
    for line in getattr(doc, "lines", []) or []:
        key = key_for_line(line)
        if len(key) < 3:
            continue
        rec = cust_map.get(key)
        if not rec or not rec.get("tmc_code"):
            continue

        status = str(getattr(line, "match_status", "") or "").lower()
        current_code = str(getattr(line, "tmc_code", "") or "").strip()
        can_override = (not current_code) or status == "no_match" or (override_fuzzy and status == "fuzzy")
        if not can_override:
            continue

        line.tmc_code = str(rec.get("tmc_code", "") or "").strip()
        line.matched_name = str(rec.get("matched_name", "") or "").strip()
        if not getattr(line, "stock_group_code", ""):
            line.stock_group_code = str(rec.get("stock_group_code", "") or "").strip()
        line.match_score = 100.0
        line.match_status = "remembered"
        applied += 1

        try:
            rec["used_count"] = int(rec.get("used_count", 0) or 0) + 1
            changed_memory = True
        except Exception:
            pass

    if changed_memory:
        _save_all(data)
    if applied:
        doc.warnings.append(f"ใช้ประวัติการแก้ไขสินค้าอัตโนมัติ {applied} รายการ")
    return applied


def stats(customer: str | None = None) -> dict[str, int]:
    data = _load_all()
    if customer:
        return {"customers": 1 if customer in data else 0, "mappings": len(data.get(customer, {}))}
    return {"customers": len(data), "mappings": sum(len(v) for v in data.values())}
