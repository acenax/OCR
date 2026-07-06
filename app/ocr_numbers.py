# -*- coding: utf-8 -*-
"""Single source of truth for parsing scanned qty/price/amount numbers.

Why this file exists
---------------------
Before this patch, four different pieces of code (ocr.py, ocr_tuning.py,
ocr_stable.py, ocr_template_v2.py) each had their *own* logic for deciding
where the decimal point goes in a scanned number, and they did not agree:

- ocr.py:            always strips punctuation and divides by 100.
- ocr_tuning.py:      trusts a "." or "," if tesseract found one, otherwise
                       only divides by 100 for qty if the digit string
                       happens to end in "00" (breaks on real cents, e.g. .08).
- ocr_stable.py:      trusts a "." or "," if found, otherwise divides by 100
                       whenever there are more than 2 digits.
- ocr_template_v2.py: tries float(token) FIRST. Because a plain digit string
                       like "1000" parses successfully as a float with no
                       exception, the "divide by 100" fallback code is never
                       reached for the most common failure case (Tesseract
                       dropping the decimal point on faint dot-matrix scans).
                       This was the main cause of rows coming out 100x too
                       large (e.g. "1000" shown instead of "10.00").

Ground truth for this customer's PO layout: qty / price / amount are ALWAYS
printed with exactly 2 decimal digits. That means the safest, most
deterministic rule is: throw away all punctuation, keep only the digits,
and always treat the last 2 digits as the fractional part. This gives the
right answer whether or not Tesseract managed to OCR the decimal point,
which is what actually varies from row to row on these faint scans.

Every OCR engine module should import `parse_scanned_number` from here
instead of maintaining its own copy of this logic.
"""
from __future__ import annotations

import re

# Non-Latin digit variants that show up occasionally depending on font/locale.
_DIGIT_TRANSLATION = str.maketrans(
    "๐๑๒๓๔๕๖๗๘๙٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹０１２３４５６７８９",
    "0123456789012345678901234567890123456789",
)

# Fields that are always printed as fixed-2-decimal money/qty on this template.
MONEY_LIKE_FIELDS = {"qty", "price", "amount", "money"}


def normalize_digits(text) -> str:
    """Convert Thai/Arabic/fullwidth digits to plain ASCII digits."""
    if text is None:
        return ""
    return str(text).translate(_DIGIT_TRANSLATION)


def parse_scanned_number(text, field: str = "amount", decimals: int = 2):
    """Parse a scanned qty/price/amount cell into a float.

    Parameters
    ----------
    text:
        Raw OCR text for the cell (may contain stray spaces, misread
        letters like O/I/l instead of 0/1, commas, dots, etc).
    field:
        One of "qty", "price", "amount", "money", "item", or anything else.
        Only fields in MONEY_LIKE_FIELDS get the fixed-decimal treatment;
        everything else (e.g. "item") is returned as a plain integer/float.
    decimals:
        Number of implied decimal digits for money-like fields. Defaults to
        2, matching this customer's printed PO format.

    Returns
    -------
    float, or None if no digits could be found at all.
    """
    if text is None:
        return None

    s = normalize_digits(text)
    # Common OCR letter/digit confusions.
    s = (
        s.replace("O", "0")
        .replace("o", "0")
        .replace("I", "1")
        .replace("l", "1")
        .replace("|", "1")
    )

    digits = re.sub(r"\D", "", s)
    if not digits:
        return None

    try:
        n = int(digits)
    except Exception:
        return None

    if field in MONEY_LIKE_FIELDS:
        # Always treat the last `decimals` digits as the fractional part.
        # This is correct regardless of whether the OCR text actually
        # contained a "." or "," — which is exactly the case that varies
        # unpredictably on these faint dot-matrix scans.
        return n / (10 ** decimals)

    return float(n)
