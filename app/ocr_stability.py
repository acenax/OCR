"""Extra stability helpers for scanned PO OCR.

This module is intentionally conservative.  It does not try to make OCR
"magically perfect"; it only repairs values when the math is strongly
supported by amount/qty/price consistency.
"""
from __future__ import annotations

import re
from typing import Any


def to_float(value: Any) -> float:
    """Parse UI/OCR numeric text safely."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(',', '')
    if not text:
        return 0.0
    # keep only first normal numeric token, but tolerate Thai/garbage around it
    m = re.search(r'-?\d+(?:\.\d+)?', text)
    if not m:
        return 0.0
    try:
        return float(m.group(0))
    except Exception:
        return 0.0


def _line_amount(line: Any) -> float:
    for name in ('amount', 'amount_ocr', 'line_amount', 'total_amount'):
        if hasattr(line, name):
            v = to_float(getattr(line, name))
            if v > 0:
                return v
    return 0.0


def _set_line_amount(line: Any, value: float) -> None:
    if hasattr(line, 'amount'):
        setattr(line, 'amount', round(float(value), 2))


def normalize_line(line: Any) -> bool:
    """Repair one line using amount = qty * price when amount OCR exists.

    Returns True if any field was changed.
    """
    changed = False
    qty = to_float(getattr(line, 'qty', 0))
    price = to_float(getattr(line, 'price', 0))
    amount = _line_amount(line)

    if qty > 0 and amount > 0:
        expected_price = round(amount / qty, 2)
        current_amount = round(qty * price, 2)
        # Repair only when price is blank/zero or line math clearly disagrees.
        if price <= 0 or abs(current_amount - amount) > max(1.0, amount * 0.015):
            setattr(line, 'price', expected_price)
            changed = True
        _set_line_amount(line, amount)
    elif qty > 0 and price > 0 and amount <= 0:
        _set_line_amount(line, round(qty * price, 2))
        changed = True

    return changed


def _sum_lines(doc: Any) -> float:
    total = 0.0
    for line in getattr(doc, 'lines', []) or []:
        amount = _line_amount(line)
        if amount > 0:
            total += amount
        else:
            total += to_float(getattr(line, 'qty', 0)) * to_float(getattr(line, 'price', 0))
    return round(total, 2)


def repair_from_total_hint(doc: Any) -> bool:
    """Try conservative repair when header/footer total is known.

    Useful for OCR mistakes like 170 -> 17 or 100 -> 0.  This uses a simple
    math-based heuristic and only changes obvious candidates.
    """
    lines = list(getattr(doc, 'lines', []) or [])
    if not lines:
        return False
    total_hint = to_float(getattr(doc, 'total', 0))
    if total_hint <= 0:
        gt = to_float(getattr(doc, 'grand_total', 0))
        if gt > 0:
            total_hint = round(gt / 1.07, 2)
    if total_hint <= 0:
        return False

    before = _sum_lines(doc)
    diff = round(total_hint - before, 2)
    if diff <= max(1.0, total_hint * 0.015):
        return False

    changed = False

    # 1) OCR often drops a trailing zero: 170 -> 17, 100 -> 10, etc.
    candidates = []
    for idx, line in enumerate(lines):
        qty = to_float(getattr(line, 'qty', 0))
        price = to_float(getattr(line, 'price', 0))
        amount = _line_amount(line)
        if amount > 0:
            continue
        if qty > 0 and 0 < price < 1000:
            delta = round(qty * (price * 10 - price), 2)
            if 0 < delta <= diff + 1:
                candidates.append((delta, idx, price, qty))
    # Apply largest useful fixes first.
    for delta, idx, price, qty in sorted(candidates, reverse=True):
        if delta <= diff + 1:
            line = lines[idx]
            setattr(line, 'price', round(price * 10, 2))
            _set_line_amount(line, round(qty * price * 10, 2))
            diff = round(diff - delta, 2)
            changed = True
            if diff <= max(1.0, total_hint * 0.005):
                break

    # 2) If a remaining difference can fill exactly one zero-price row, use it.
    if diff > max(1.0, total_hint * 0.005):
        zero_rows = []
        for idx, line in enumerate(lines):
            qty = to_float(getattr(line, 'qty', 0))
            price = to_float(getattr(line, 'price', 0))
            if qty > 0 and price <= 0:
                zero_rows.append((idx, qty))
        if len(zero_rows) == 1:
            idx, qty = zero_rows[0]
            repaired_price = round(diff / qty, 2)
            if repaired_price > 0:
                setattr(lines[idx], 'price', repaired_price)
                _set_line_amount(lines[idx], round(qty * repaired_price, 2))
                changed = True

    return changed


def normalize_po_document(doc: Any, preserve_printed_total: bool = True) -> Any:
    """Normalize all lines and totals in a PODocument-like object."""
    changed = False
    for line in getattr(doc, 'lines', []) or []:
        changed = normalize_line(line) or changed

    changed = repair_from_total_hint(doc) or changed

    line_total = _sum_lines(doc)
    printed_total = to_float(getattr(doc, 'total', 0))
    printed_grand = to_float(getattr(doc, 'grand_total', 0))

    # Prefer consistent line total after repair.  If the existing printed total
    # is close enough, keep it; otherwise use the repaired line total so the UI
    # does not keep wrong OCR math.
    if line_total > 0:
        if preserve_printed_total and printed_total > 0 and abs(printed_total - line_total) <= max(1.0, line_total * 0.01):
            total = printed_total
        else:
            total = line_total
        setattr(doc, 'total', round(total, 2))
        setattr(doc, 'vat', round(total * 0.07, 2))
        if printed_grand > 0 and abs(printed_grand - (total + total * 0.07)) <= max(1.0, total * 0.01):
            setattr(doc, 'grand_total', round(printed_grand, 2))
        else:
            setattr(doc, 'grand_total', round(total + total * 0.07, 2))

    if changed:
        warnings = list(getattr(doc, 'warnings', []) or [])
        msg = 'ระบบซ่อมราคา/ยอดเงินจาก Amount และตรวจสมดุลยอดรวมให้อัตโนมัติแล้ว'
        if msg not in warnings:
            warnings.append(msg)
        setattr(doc, 'warnings', warnings)
    return doc
