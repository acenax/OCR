# -*- coding: utf-8 -*-
"""Utilities to normalize all digit styles to Arabic numerals 0-9."""
from __future__ import annotations

_TH_DIGITS = "๐๑๒๓๔๕๖๗๘๙"
_ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
_EXT_ARABIC_INDIC = "۰۱۲۳۴۵۶۷۸۹"
_FULL_WIDTH = "０１２３４５６７８９"
_ASCII = "0123456789"
_TRANS = str.maketrans({
    **{c: _ASCII[i] for i, c in enumerate(_TH_DIGITS)},
    **{c: _ASCII[i] for i, c in enumerate(_ARABIC_INDIC)},
    **{c: _ASCII[i] for i, c in enumerate(_EXT_ARABIC_INDIC)},
    **{c: _ASCII[i] for i, c in enumerate(_FULL_WIDTH)},
})


def to_arabic_digits(value):
    """Convert Thai/Arabic-Indic/full-width digits in any value to ASCII digits."""
    if value is None:
        return ""
    return str(value).translate(_TRANS)


def normalize_obj_digits(obj):
    """Normalize common PO document / line fields in-place."""
    if obj is None:
        return obj
    text_fields = (
        "item_no", "product_code_raw", "description_raw", "tmc_code",
        "matched_name", "stock_group_code", "match_status",
        "source_pdf", "customer", "po_no", "po_date", "po_date_raw",
    )
    for name in text_fields:
        if hasattr(obj, name):
            try:
                setattr(obj, name, to_arabic_digits(getattr(obj, name)))
            except Exception:
                pass
    if hasattr(obj, "warnings") and isinstance(getattr(obj, "warnings"), list):
        try:
            obj.warnings = [to_arabic_digits(x) for x in obj.warnings]
        except Exception:
            pass
    if hasattr(obj, "lines"):
        for line in list(getattr(obj, "lines") or []):
            normalize_obj_digits(line)
    return obj
