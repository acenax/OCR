"""Shared numeric parsing/formatting helpers for OCR.

The old code had several different number parsers.  This module is the
single source of truth for qty / price / amount fields.
"""

from __future__ import annotations

import re
from typing import Any

_DIGIT_TRANSLATION = str.maketrans(
    "๐๑๒๓๔๕๖๗๘๙٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹０１２３４５６７８۹",
    "0123456789012345678901234567890123456789",
)


def normalize_digits(value: Any) -> str:
    if value is None:
        return ""
    return str(value).translate(_DIGIT_TRANSLATION)


def clean_number_text(value: Any) -> str:
    """Keep only characters that can be part of a number."""
    s = normalize_digits(value)
    s = s.replace("O", "0").replace("o", "0")
    s = s.replace("l", "1").replace("I", "1").replace("|", "1")
    s = s.replace("S", "5").replace("s", "5")
    return re.sub(r"[^0-9,\.\-]", "", s)


def _float_from_visible_decimal(s: str) -> float | None:
    m = re.search(r"-?[0-9][0-9,]*(?:\.[0-9]{1,4})", s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except Exception:
        return None


def parse_scanned_number(value: Any, field: str = "amount", number_mode: str = "auto") -> float | None:
    """Parse OCR number text.

    field: qty | price | amount | total
    number_mode:
      - auto: prefer visible decimals; otherwise infer safely.
      - fixed2: for scans that often lose decimal points.  Example: 510000 -> 5100.00
      - decimal: trust visible decimal points and plain integers.
    """
    raw = clean_number_text(value)
    if not raw:
        return None

    visible = _float_from_visible_decimal(raw)
    if visible is not None:
        return visible

    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None

    try:
        n = int(digits)
    except Exception:
        return None

    field = (field or "amount").lower()
    mode = (number_mode or "auto").lower()

    # Quantity often appears as 1, 10, 15, 60.  If no decimal was visible and
    # the digit length is short, plain integer is safer than /100.
    if field in {"qty", "quantity"}:
        if mode == "fixed2" and len(digits) > 2:
            return n / 100.0
        return float(n)

    # Money fields: if fixed2 or long digit string, assume two hidden decimals.
    # This fixes OCR like 2603750 -> 26,037.50 and 510000 -> 5,100.00.
    if mode == "fixed2" or len(digits) >= 4:
        return n / 100.0

    return float(n)


def format_money(value: Any) -> str:
    try:
        return f"{float(str(value).replace(',', '')):,.2f}"
    except Exception:
        return "0.00"


def format_qty(value: Any) -> str:
    try:
        v = float(str(value).replace(',', ''))
    except Exception:
        return "0"
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v))}"
    return f"{v:,.4f}".rstrip("0").rstrip(".")
