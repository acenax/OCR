"""Validate Product Details.xlsx before it is used for OCR matching.

This module is intentionally independent from the UI so it can later be used by
CLI, scheduled checks, or unit tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import pandas as pd


@dataclass
class ProductIssue:
    severity: str  # critical | review | info
    row: int
    column: str
    message: str
    value: str = ""


def _text(v: Any) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass
    return str(v).strip()


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9ก-๙]", "", str(s).upper())


def _looks_like_tmc_code(v: str) -> bool:
    """Loose sanity check only; keep it permissive to avoid false alarms."""
    if not v:
        return False
    if len(v) > 80:
        return False
    # TMC codes are usually compact. Spaces / long Thai descriptions often mean
    # the wrong column was selected or source data is corrupted.
    if len(v.split()) >= 4:
        return False
    return bool(re.search(r"[A-Za-z0-9]", v))


def validate_product_details(path: str | Path) -> tuple[dict[str, Any], list[ProductIssue]]:
    p = Path(path) if path else Path("")
    issues: list[ProductIssue] = []
    summary: dict[str, Any] = {
        "path": str(p),
        "exists": p.exists(),
        "rows": 0,
        "columns": [],
        "tmc_col": "",
        "name_cols": [],
        "price_col": "",
        "critical": 0,
        "review": 0,
        "info": 0,
    }

    if not p.exists():
        issues.append(ProductIssue("critical", 0, "file", "ไม่พบไฟล์ Product Details.xlsx", str(p)))
        summary["critical"] = 1
        return summary, issues

    try:
        df = pd.read_excel(p)
    except Exception as exc:
        issues.append(ProductIssue("critical", 0, "file", f"เปิดไฟล์ Excel ไม่ได้: {exc}", str(p)))
        summary["critical"] = 1
        return summary, issues

    summary["rows"] = int(len(df))
    summary["columns"] = [str(c) for c in df.columns]

    if df.empty:
        issues.append(ProductIssue("critical", 0, "file", "ไฟล์ Product Details ไม่มีข้อมูล", str(p)))
        summary["critical"] = 1
        return summary, issues

    tmc_candidates = [c for c in df.columns if "tmc" in str(c).lower()]
    if not tmc_candidates:
        issues.append(ProductIssue("critical", 1, "tmc_code", "ไม่พบคอลัมน์ tmc_code หรือคอลัมน์ที่มีคำว่า tmc", ""))
        summary["critical"] = 1
        return summary, issues

    tmc_col = tmc_candidates[0]
    summary["tmc_col"] = str(tmc_col)
    if len(tmc_candidates) > 1:
        issues.append(ProductIssue("review", 1, "tmc_code", "พบคอลัมน์ที่มีคำว่า tmc มากกว่า 1 คอลัมน์ ระบบใช้คอลัมน์แรก", ", ".join(map(str, tmc_candidates))))

    cols = list(df.columns)
    tmc_idx = cols.index(tmc_col)
    name_cols = cols[:tmc_idx] or [c for c in cols if c != tmc_col and "price" not in str(c).lower() and "ราคา" not in str(c)]
    summary["name_cols"] = [str(c) for c in name_cols]
    if not name_cols:
        issues.append(ProductIssue("critical", 1, "name", "ไม่พบคอลัมน์ชื่อสินค้า/รหัสลูกค้าสำหรับใช้จับคู่", ""))

    price_col = None
    for c in df.columns:
        cl = str(c).lower()
        if "price" in cl or "ราคา" in str(c):
            price_col = c
            break
    if price_col is not None:
        summary["price_col"] = str(price_col)

    seen_tmc: dict[str, int] = {}
    seen_name: dict[str, tuple[int, str]] = {}

    for idx, row in df.iterrows():
        excel_row = int(idx) + 2
        tmc = _text(row.get(tmc_col))
        if not tmc:
            issues.append(ProductIssue("critical", excel_row, str(tmc_col), "tmc_code ว่าง", ""))
        elif not _looks_like_tmc_code(tmc):
            issues.append(ProductIssue("review", excel_row, str(tmc_col), "tmc_code ดูผิดรูปแบบ อาจเป็นชื่อสินค้าหรือข้อมูลผิดคอลัมน์", tmc[:120]))

        key_tmc = _norm(tmc)
        if key_tmc:
            if key_tmc in seen_tmc:
                issues.append(ProductIssue("review", excel_row, str(tmc_col), f"tmc_code ซ้ำกับแถว {seen_tmc[key_tmc]}", tmc))
            else:
                seen_tmc[key_tmc] = excel_row

        names = [_text(row.get(c)) for c in name_cols]
        names = [n for n in names if n]
        if not names:
            issues.append(ProductIssue("critical", excel_row, "name", "ไม่มีชื่อสินค้า/รหัสสินค้าลูกค้าให้ระบบจับคู่", ""))
        for name in names:
            key_name = _norm(name)
            if len(key_name) < 3:
                issues.append(ProductIssue("review", excel_row, "name", "ชื่อสินค้า/รหัสลูกค้าสั้นเกินไป อาจทำให้จับคู่ผิด", name))
                continue
            old = seen_name.get(key_name)
            if old and old[1] != tmc:
                issues.append(ProductIssue("review", excel_row, "name", f"ชื่อสินค้า/รหัสลูกค้าซ้ำกับแถว {old[0]} แต่ tmc_code ไม่เหมือนกัน", name))
            else:
                seen_name[key_name] = (excel_row, tmc)

        if price_col is not None:
            raw_price = _text(row.get(price_col))
            if raw_price:
                try:
                    price = float(raw_price.replace(",", ""))
                    if price <= 0:
                        issues.append(ProductIssue("review", excel_row, str(price_col), "ราคาน้อยกว่าหรือเท่ากับ 0", raw_price))
                except Exception:
                    issues.append(ProductIssue("review", excel_row, str(price_col), "ราคาไม่ใช่ตัวเลข", raw_price))

    for sev in ("critical", "review", "info"):
        summary[sev] = sum(1 for i in issues if i.severity == sev)
    return summary, issues
