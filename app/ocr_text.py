"""Text cleaning helpers for OCR output."""

from __future__ import annotations

import re
from typing import Any

from .ocr_numbers import normalize_digits


def clean_ocr_text(value: Any) -> str:
    s = normalize_digits(value)
    s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    s = re.sub(r"[|¦]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_product_code(value: Any) -> str:
    s = clean_ocr_text(value).upper()
    # Common OCR confusions in customer/product codes.
    s = s.replace(" ", "")
    s = s.replace("O", "0")
    s = s.replace("I", "1").replace("L", "1")
    s = re.sub(r"[^A-Z0-9_\-\./]", "", s)
    return s


def clean_part_description(value: Any) -> str:
    s = clean_ocr_text(value).upper()
    # Keep symbols that are common in tool/insert part numbers.
    s = re.sub(r"[^A-Z0-9ก-๙_\-\./\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
