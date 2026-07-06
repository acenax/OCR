"""Helpers for repairing OCR amount/price/total inconsistencies.

This module is intentionally UI-independent.  The common OCR failure for CMT-like
PO scans is that UNIT PRICE loses a digit/decimal while the AMOUNT column is read
correctly.  Example: qty=10, price=17, amount=1700 should become price=170.
"""
from __future__ import annotations

from typing import Iterable


def money(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(str(value).replace(",", "").strip() or default)
    except Exception:
        return default


def _near_int(value: float, tolerance: float = 0.05) -> bool:
    return abs(value - round(value)) <= tolerance


def _tolerance(base: float) -> float:
    return max(1.0, abs(base) * 0.02)


def line_calc_amount(line) -> float:
    return round(money(getattr(line, "qty", 0)) * money(getattr(line, "price", 0)), 2)


def line_best_amount(line) -> float:
    amount = money(getattr(line, "amount", 0))
    if amount > 0:
        return round(amount, 2)
    return line_calc_amount(line)


def repair_line(line) -> list[str]:
    """Repair one POLine in-place and return notes."""
    notes: list[str] = []
    qty = money(getattr(line, "qty", 0))
    price = money(getattr(line, "price", 0))
    amount = money(getattr(line, "amount", 0))

    # Case 1: Amount and Qty are available.  Unit price can be derived reliably.
    if amount > 0 and qty > 0:
        inferred_price = round(amount / qty, 4)
        calc = round(qty * price, 2)
        if price <= 0 or abs(calc - amount) > _tolerance(amount):
            if 0 < inferred_price < 1_000_000_000:
                old = price
                setattr(line, "price", inferred_price)
                notes.append(f"ซ่อมราคา {old:g} -> {inferred_price:g} จากยอดเงิน/จำนวน")
        # Keep amount rounded and normalized.
        setattr(line, "amount", round(amount, 2))
        return notes

    # Case 2: Amount and Price are available, Qty is missing/bad.  Derive qty
    # only when it looks like an integer quantity.
    if amount > 0 and price > 0 and qty <= 0:
        inferred_qty = amount / price
        if 0 < inferred_qty < 1_000_000 and _near_int(inferred_qty):
            setattr(line, "qty", float(round(inferred_qty)))
            notes.append(f"ซ่อมจำนวน -> {round(inferred_qty):g} จากยอดเงิน/ราคา")
        setattr(line, "amount", round(amount, 2))
        return notes

    # Case 3: Amount is missing but qty and price are available. Fill amount so
    # the UI/validator can compare consistently.
    if amount <= 0 and qty > 0 and price > 0:
        calc = round(qty * price, 2)
        setattr(line, "amount", calc)
        notes.append(f"เติมยอดเงินจากจำนวน×ราคา = {calc:,.2f}")
    return notes


def repair_document(doc, *, update_header: bool = True) -> list[str]:
    """Repair all lines and optionally update doc totals from line amounts.

    For OCR documents, the safest total after repair is the sum of line AMOUNT
    values when present; otherwise qty*price.  This prevents a wrong unit price
    from overwriting the true document total.
    """
    notes: list[str] = []
    lines = list(getattr(doc, "lines", []) or [])
    for idx, line in enumerate(lines, 1):
        for note in repair_line(line):
            notes.append(f"แถว {idx}: {note}")

    if update_header and lines:
        total = round(sum(line_best_amount(line) for line in lines), 2)
        if total > 0:
            old_total = money(getattr(doc, "total", 0))
            if old_total and abs(old_total - total) > _tolerance(total):
                notes.append(f"ปรับรวมราคาสินค้า {old_total:,.2f} -> {total:,.2f} จากยอดเงินรายแถว")
            setattr(doc, "total", total)
            vat = round(total * 0.07, 2)
            setattr(doc, "vat", vat)
            setattr(doc, "grand_total", round(total + vat, 2))

    # Store repair notes as warnings once, but keep it concise.
    try:
        warnings = list(getattr(doc, "warnings", []) or [])
        for note in notes[:8]:
            msg = "Auto repair: " + note
            if msg not in warnings:
                warnings.append(msg)
        if len(notes) > 8:
            msg = f"Auto repair: และอีก {len(notes)-8} รายการ"
            if msg not in warnings:
                warnings.append(msg)
        setattr(doc, "warnings", warnings)
    except Exception:
        pass
    return notes


def document_line_total(doc) -> float:
    return round(sum(line_best_amount(line) for line in (getattr(doc, "lines", []) or [])), 2)
