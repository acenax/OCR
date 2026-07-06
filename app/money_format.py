# -*- coding: utf-8 -*-
"""Runtime money formatting helpers for the OCR desktop app.

This module is intentionally lightweight and safe to import from the UI layer.
It formats only fields that look like money/price/amount/total/VAT, avoiding PO no., dates, qty, and item codes.
"""
from __future__ import annotations

import re
from typing import Any

_INSTALLED = False
_DIGIT_TRANS = str.maketrans({
    "๐": "0", "๑": "1", "๒": "2", "๓": "3", "๔": "4",
    "๕": "5", "๖": "6", "๗": "7", "๘": "8", "๙": "9",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
    "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
})

_MONEY_KEYWORDS = (
    "ราคา", "ยอดเงิน", "จำนวนเงิน", "เงิน", "amount", "price", "unit price",
    "total", "grand", "vat", "tax", "มูลค่า", "รวมราคา", "รวมสินค้า", "รวมทั้งสิ้น",
)
_NON_MONEY_HINTS = (
    "เลขที่", "วันที่", "date", "po", "ลำดับ", "item", "รหัส", "code", "qty", "quantity",
    "จำนวน", "ลูกค้า", "ชื่อ", "name", "เดือน", "ปี", "status", "สถานะ", "path", "ไฟล์",
)


def normalize_digits(value: Any) -> str:
    """Convert Thai/Arabic-Indic/full-width digits to normal 0-9."""
    if value is None:
        return ""
    return str(value).translate(_DIGIT_TRANS)


def parse_number(value: Any) -> float | None:
    """Parse a money-like value. Returns None when it is not numeric."""
    text = normalize_digits(value).strip()
    if not text:
        return None
    text = text.replace("\u00a0", " ").replace(" ", "")
    text = text.replace(",", "")
    text = text.replace("฿", "").replace("บาท", "")
    # Keep only the first normal numeric token. This protects labels like "VAT 1,822.63".
    m = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def format_money(value: Any, decimals: int = 2) -> str:
    """Format money with thousands comma and fixed decimals."""
    num = parse_number(value)
    if num is None:
        return normalize_digits(value)
    return f"{num:,.{decimals}f}"


def is_money_header(header: Any) -> bool:
    """Return True only for headers/labels that are money fields."""
    h = normalize_digits(header).strip().lower()
    if not h:
        return False
    if not any(k in h for k in _MONEY_KEYWORDS):
        return False
    # "จำนวนเงิน" is money, but "จำนวน" / "qty" alone is not.
    if "จำนวนเงิน" in h or "ยอดเงิน" in h:
        return True
    if "จำนวน" in h and "เงิน" not in h:
        return False
    if "qty" in h or "quantity" in h:
        return False
    if "รหัส" in h or "code" in h:
        return False
    return True


def looks_like_decimal_money_text(text: Any) -> bool:
    """Safe heuristic for QLineEdit: format only clear decimal money values, not PO/date/qty."""
    s = normalize_digits(text).strip()
    if not s:
        return False
    if "-" in s or "/" in s or ":" in s:
        return False
    # Require decimal part so pure PO no. like 69110614 will not become 69,110,614.00.
    return bool(re.fullmatch(r"[-+]?\d{1,3}(?:,?\d{3})*(?:\.\d{1,6})|[-+]?\d+\.\d{1,6}", s.replace(" ", "")))


def _header_text(table: Any, col: int) -> str:
    try:
        item = table.horizontalHeaderItem(col)
        return item.text() if item else ""
    except Exception:
        return ""


def _format_item_for_column(table: Any, col: int, item: Any) -> None:
    if item is None:
        return
    header = _header_text(table, col)
    if not is_money_header(header):
        return
    try:
        raw = item.text()
        if raw is None or raw == "":
            return
        new_text = format_money(raw)
        if new_text != raw:
            item.setText(new_text)
        # right align numeric money columns
        try:
            from PySide6.QtCore import Qt
            item.setTextAlignment(int(Qt.AlignRight | Qt.AlignVCenter))
        except Exception:
            pass
    except Exception:
        return


def format_table_money_columns(table: Any) -> None:
    """Format existing QTableWidget cells whose headers are money-like."""
    try:
        rows = table.rowCount()
        cols = table.columnCount()
    except Exception:
        return
    for c in range(cols):
        if not is_money_header(_header_text(table, c)):
            continue
        for r in range(rows):
            try:
                _format_item_for_column(table, c, table.item(r, c))
            except Exception:
                continue


def format_money_lineedit_text(text: Any) -> str:
    """Format a line edit value only when it is clearly a decimal money value."""
    s = normalize_digits(text)
    if looks_like_decimal_money_text(s):
        return format_money(s)
    return s


def install_money_format_patch() -> None:
    """Monkey patch PySide6 widgets to display money consistently across the app."""
    global _INSTALLED
    if _INSTALLED:
        return
    try:
        from PySide6.QtWidgets import QTableWidget, QLineEdit
    except Exception:
        return

    # Patch QTableWidget.setItem so every new money cell is formatted automatically.
    if not getattr(QTableWidget, "_tmc_money_format_patched", False):
        _orig_set_item = QTableWidget.setItem

        def _patched_set_item(self, row, column, item):
            try:
                _format_item_for_column(self, column, item)
            except Exception:
                pass
            return _orig_set_item(self, row, column, item)

        QTableWidget.setItem = _patched_set_item
        QTableWidget._tmc_money_format_patched = True

    # Patch QLineEdit.setText for header totals/VAT. It only formats decimal-looking values.
    if not getattr(QLineEdit, "_tmc_money_format_patched", False):
        _orig_set_text = QLineEdit.setText

        def _patched_set_text(self, text):
            try:
                text = format_money_lineedit_text(text)
            except Exception:
                pass
            return _orig_set_text(self, text)

        QLineEdit.setText = _patched_set_text
        QLineEdit._tmc_money_format_patched = True

    _INSTALLED = True


# Convenience alias for code that wants explicit formatting.
money = format_money
