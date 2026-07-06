# -*- coding: utf-8 -*-
"""Shared helpers for cleaning up OCR'd Thai/English text (code, description,
item columns) and for stripping table gridlines out of field crops before
they're sent to Tesseract.

Why this file exists
---------------------
Product code / description columns on this customer's PO are almost always
pure Latin letters, digits, and punctuation (e.g. "16ERAG60-TC-GM3225"),
but every OCR engine module runs those cells with the mixed "tha+eng"
language model. Two things then conspire to corrupt the text:

1. Field crops are padded OUTWARD by a few px (ocr_tuning.py, ocr_stable.py,
   ocr_template_v2.py all do this) so characters right at the taught box
   edge don't get clipped. If the taught box sits close to a table
   gridline, that padding pulls a sliver of the gridline into the crop.
2. The existing "remove colored lines" preprocessing only strips
   high-saturation (colored ink) pixels. Ordinary black/grey table borders
   pass straight through untouched.

When Tesseract's tha+eng model is handed a sliver of gridline/border noise,
it will sometimes "recognize" it as the Thai character(s) that most
resemble it, producing output like "ปี16 TRAG 60-TC-GM3225" instead of the
correct "16 TRAG 60-TC-GM3225".

`remove_table_gridlines` strips long straight dark lines (any colour,
not just saturated ones) before OCR. `clean_ocr_text` is a second line of
defence applied after OCR: it strips a short isolated Thai fragment glued
to the front/back of an otherwise pure Latin/numeric string, since that
combination essentially never happens in real product descriptions on
this template.
"""
from __future__ import annotations

import re

try:
    import cv2
    import numpy as np
except Exception:  # pragma: no cover
    cv2 = None
    np = None

_THAI_RANGE = "\u0E00-\u0E7F"


def remove_table_gridlines(rgb_or_gray):
    """Paint over long straight dark lines (table borders) with white.

    Works on either a 3-channel RGB array or a single-channel grayscale
    array. Safe to call even if the image has no gridlines in it.
    """
    if cv2 is None or np is None:
        return rgb_or_gray
    arr = rgb_or_gray.copy()
    if arr.ndim == 3:
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    else:
        gray = arr

    # Dark pixels only (table borders are near-black regardless of any
    # colour cast in the scan) — unlike the colour-artifact filter, this
    # does NOT look at saturation, so it also catches plain black/grey
    # gridlines that the colour filter misses.
    dark = (gray < 120).astype("uint8") * 255

    h, w = gray.shape[:2]
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, w // 3), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, h // 3)))
    h_lines = cv2.morphologyEx(dark, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(dark, cv2.MORPH_OPEN, v_kernel)
    lines = cv2.bitwise_or(h_lines, v_lines)
    if lines.any():
        lines = cv2.dilate(lines, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)
        if arr.ndim == 3:
            arr[lines > 0] = [255, 255, 255]
        else:
            arr[lines > 0] = 255
    return arr


def inset_box(box, w: int, h: int, inset_px: int = 2):
    """Shrink a (x1, y1, x2, y2) pixel box inward instead of outward.

    Field crops used to always be padded OUTWARD (to avoid clipping
    characters near the taught edge), which is exactly what pulls in
    adjacent table gridlines. Insetting slightly is safer for this
    template because cells have generous internal margins; combined with
    `remove_table_gridlines` above, this meaningfully cuts down on stray
    border pixels reaching Tesseract.
    """
    x1, y1, x2, y2 = box
    x1 = min(x1 + inset_px, x2 - 4)
    y1 = min(y1 + inset_px, y2 - 4)
    x2 = max(x2 - inset_px, x1 + 4)
    y2 = max(y2 - inset_px, y1 + 4)
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(w, x2)
    y2 = min(h, y2)
    return x1, y1, x2, y2


def clean_ocr_text(text: str) -> str:
    """Strip stray hallucinated Thai fragments from otherwise Latin/numeric text.

    Only touches text that looks like: [1-2 Thai chars][Latin/digit content
    making up most of the string]. Real Thai product descriptions (multiple
    Thai words, mostly Thai characters) are left untouched.
    """
    if not text:
        return text
    s = str(text).strip()
    s = s.replace("\u200b", "").replace("\ufeff", "")
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return s

    for pattern, group in (
        (rf"^([{_THAI_RANGE}]{{1,2}})\s*(?=[A-Za-z0-9])", "leading"),
        (rf"(?<=[A-Za-z0-9])\s*([{_THAI_RANGE}]{{1,2}})$", "trailing"),
    ):
        m = re.search(pattern, s)
        if not m:
            continue
        if group == "leading":
            rest = s[m.end():]
        else:
            rest = s[:m.start()]
        thai_in_rest = len(re.findall(rf"[{_THAI_RANGE}]", rest))
        latin_digit_in_rest = len(re.findall(r"[A-Za-z0-9]", rest))
        # Only strip when the remainder is clearly Latin/numeric (this
        # template's product descriptions), not genuine Thai text.
        if latin_digit_in_rest >= 4 and thai_in_rest == 0:
            s = rest.strip(" -_/")
    return s
