
"""PaddleOCR-first template OCR engine.

This replaces the old Tesseract-first template reader.  When a customer has
been taught column boxes, PaddleOCR is used as the primary engine and the
returned bounding boxes are mapped into the taught columns/rows.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import cv2
import numpy as np
import pytesseract
from PIL import Image

from .models import PODocument, POLine
from .ocr_image_filters import enhance_for_ocr
from .ocr_numbers import normalize_digits, parse_scanned_number
from .ocr_text import clean_part_description, clean_product_code, clean_ocr_text
from .paddle_ocr_engine import OCRWord, extract_words, words_to_text

COLUMN_KEYS = ["item", "code", "desc", "qty", "price", "amount"]
NUMERIC_KEYS = {"qty", "price", "amount"}

TESS_CONFIGS = {
    "item": "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789",
    "code": "--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_/.",
    "desc": "--oem 3 --psm 6",
    "qty": "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.,-",
    "price": "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.,-",
    "amount": "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.,-",
}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _profile_boxes(profile: dict | None, width: int, height: int) -> dict[str, tuple[int, int, int, int]]:
    profile = profile or {}
    out: dict[str, tuple[int, int, int, int]] = {}

    boxes = profile.get("boxes") or profile.get("field_boxes") or profile.get("rects")
    if isinstance(boxes, dict):
        for key, raw in boxes.items():
            if key not in COLUMN_KEYS:
                continue
            if isinstance(raw, dict):
                vals = [
                    raw.get("left", raw.get("x1", 0)),
                    raw.get("top", raw.get("y1", 0)),
                    raw.get("right", raw.get("x2", 0)),
                    raw.get("bottom", raw.get("y2", 0)),
                ]
            else:
                vals = list(raw)[:4] if isinstance(raw, (list, tuple)) else []
            if len(vals) < 4:
                continue
            l, t, r, b = [_as_float(v) for v in vals]
            if max(abs(l), abs(t), abs(r), abs(b)) <= 1.5:
                l, r = l * width, r * width
                t, b = t * height, b * height
            l, r = sorted((l, r))
            t, b = sorted((t, b))
            if r - l >= 4 and b - t >= 4:
                out[key] = (max(0, int(l)), max(0, int(t)), min(width, int(r)), min(height, int(b)))
    if out:
        return out

    columns = profile.get("columns") or {}
    if isinstance(columns, dict):
        top = _as_float(profile.get("data_top_frac", 0.28))
        bottom = _as_float(profile.get("bottom_frac", 0.78))
        if top <= 1.5:
            top *= height
        if bottom <= 1.5:
            bottom *= height
        for key, raw in columns.items():
            if key not in COLUMN_KEYS:
                continue
            try:
                l, r = float(raw[0]), float(raw[1])
            except Exception:
                continue
            if max(abs(l), abs(r)) <= 1.5:
                l, r = l * width, r * width
            l, r = sorted((l, r))
            if r - l >= 4:
                out[key] = (max(0, int(l)), max(0, int(top)), min(width, int(r)), min(height, int(bottom)))
    return out


def _crop(image: Image.Image, box: tuple[int, int, int, int], pad: int = 2) -> Image.Image:
    w, h = image.size
    l, t, r, b = box
    return image.crop((max(0, l - pad), max(0, t - pad), min(w, r + pad), min(h, b + pad)))


def _prepare_image(image: Image.Image, filter_mode: str, remove_lines: bool) -> Image.Image:
    arr = enhance_for_ocr(image, mode=filter_mode, remove_color_lines=remove_lines)
    if isinstance(arr, np.ndarray):
        if arr.ndim == 2:
            return Image.fromarray(arr).convert("RGB")
        return Image.fromarray(arr).convert("RGB")
    return image.convert("RGB")


def _intersects_box(word: OCRWord, box: tuple[int, int, int, int], y_band: tuple[int, int] | None = None) -> bool:
    l, t, r, b = box
    cy = word.cy
    cx = word.cx
    if y_band is not None:
        yt, yb = y_band
        if not (yt <= cy <= yb):
            return False
    return (l - 3) <= cx <= (r + 3) and (t - 5) <= cy <= (b + 5)


def _words_in_cell(words: list[OCRWord], box: tuple[int, int, int, int], y_band: tuple[int, int]) -> list[OCRWord]:
    return sorted([w for w in words if _intersects_box(w, box, y_band)], key=lambda w: (w.top, w.left))


def _cell_text(words: list[OCRWord], key: str) -> tuple[str, float]:
    if not words:
        return "", -1.0
    text = " ".join(w.text for w in words).strip()
    confs = [w.confidence for w in words if w.confidence >= 0]
    conf = float(sum(confs) / len(confs)) if confs else -1.0
    text = normalize_digits(text)
    if key == "code":
        return clean_product_code(text), conf
    if key == "desc":
        return clean_part_description(text), conf
    return clean_ocr_text(text), conf


def _fallback_tesseract_cell(image: Image.Image, key: str, lang: str, filter_mode: str, remove_lines: bool) -> tuple[str, float]:
    config = TESS_CONFIGS.get(key, "--oem 3 --psm 7")
    use_lang = "eng" if key in {"item", "code", "qty", "price", "amount"} else (lang or "tha+eng")
    proc = enhance_for_ocr(image, mode=filter_mode, remove_color_lines=remove_lines)
    try:
        txt = pytesseract.image_to_string(proc, lang=use_lang, config=config)
    except Exception:
        txt = ""
    txt = clean_ocr_text(normalize_digits(txt))
    return txt, -1.0


def _row_bands_from_words(words: list[OCRWord], boxes: dict[str, tuple[int, int, int, int]]) -> list[tuple[int, int]]:
    if not boxes:
        return []
    x1 = min(b[0] for b in boxes.values())
    y1 = min(b[1] for b in boxes.values())
    x2 = max(b[2] for b in boxes.values())
    y2 = max(b[3] for b in boxes.values())
    table_words = [w for w in words if (x1 - 8) <= w.cx <= (x2 + 8) and (y1 - 8) <= w.cy <= (y2 + 8)]
    if not table_words:
        return []
    table_words.sort(key=lambda w: w.cy)
    heights = [w.height for w in table_words if w.height > 0]
    tol = max(12.0, (float(np.median(heights)) if heights else 14.0) * 0.90)
    clusters: list[list[OCRWord]] = []
    for w in table_words:
        if not clusters or abs(w.cy - float(np.mean([x.cy for x in clusters[-1]]))) > tol:
            clusters.append([w])
        else:
            clusters[-1].append(w)
    bands: list[tuple[int, int]] = []
    for cl in clusters:
        top = int(min(w.top for w in cl)) - 3
        bot = int(max(w.bottom for w in cl)) + 3
        if bot - top >= 8:
            bands.append((max(y1, top), min(y2, bot)))
    # remove too-close duplicate bands
    out: list[tuple[int, int]] = []
    for band in bands:
        if not out or abs(((band[0]+band[1])/2) - ((out[-1][0]+out[-1][1])/2)) > 8:
            out.append(band)
        else:
            out[-1] = (min(out[-1][0], band[0]), max(out[-1][1], band[1]))
    return out


def _row_bands_from_grid(image: Image.Image, boxes: dict[str, tuple[int, int, int, int]], filter_mode: str, remove_lines: bool) -> list[tuple[int, int]]:
    if not boxes:
        return []
    x1 = min(b[0] for b in boxes.values())
    y1 = min(b[1] for b in boxes.values())
    x2 = max(b[2] for b in boxes.values())
    y2 = max(b[3] for b in boxes.values())
    body = _crop(image, (x1, y1, x2, y2), pad=0)
    arr = np.array(body.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, (x2 - x1) // 8), 1))
    hor = cv2.morphologyEx(inv, cv2.MORPH_OPEN, hk)
    rowsum = hor.sum(axis=1) / 255.0
    ys = [i for i, v in enumerate(rowsum) if v > (x2 - x1) * 0.25]
    groups: list[list[int]] = []
    for y in ys:
        if not groups or y - groups[-1][-1] > 4:
            groups.append([y])
        else:
            groups[-1].append(y)
    lines = [int(np.mean(g)) for g in groups]
    bands: list[tuple[int, int]] = []
    if len(lines) >= 2:
        for a, b in zip(lines, lines[1:]):
            if b - a >= 12:
                bands.append((y1 + a + 2, y1 + b - 2))
    return bands


def repair_line_numbers(line: POLine) -> POLine:
    q = float(line.qty or 0)
    p = float(line.price or 0)
    a = float(line.amount or 0)
    if q > 0 and a > 0:
        derived_price = a / q
        expected = q * p if p > 0 else 0
        if p <= 0 or abs(expected - a) > max(1.0, abs(a) * 0.03):
            p = derived_price
    elif q > 0 and p > 0 and a <= 0:
        a = q * p
    elif a > 0 and p > 0 and q <= 0:
        q = a / p
    return replace(line, qty=round(q, 4), price=round(p, 4), amount=round(a, 2))


def _line_from_row(image: Image.Image, words: list[OCRWord], boxes: dict[str, tuple[int, int, int, int]], row_band: tuple[int, int], lang: str, number_mode: str, filter_mode: str, remove_lines: bool) -> POLine | None:
    raw: dict[str, str] = {}
    conf: dict[str, float] = {}
    for key, box in boxes.items():
        cell_words = _words_in_cell(words, box, row_band)
        txt, cf = _cell_text(cell_words, key)
        # If Paddle missed a tiny numeric/code cell, fallback only for that crop.
        if not txt and key in {"item", "code", "qty", "price", "amount"}:
            l, _t, r, _b = box
            txt, cf = _fallback_tesseract_cell(_crop(image, (l, row_band[0], r, row_band[1]), pad=3), key, lang, filter_mode, remove_lines)
        raw[key] = txt
        conf[key] = cf

    item = clean_ocr_text(raw.get("item", ""))
    code = clean_product_code(raw.get("code", ""))
    desc = clean_part_description(raw.get("desc", ""))
    qty = parse_scanned_number(raw.get("qty", ""), "qty", number_mode) or 0.0
    price = parse_scanned_number(raw.get("price", ""), "price", number_mode) or 0.0
    amount = parse_scanned_number(raw.get("amount", ""), "amount", number_mode) or 0.0

    joined = " ".join([item, code, desc]).upper()
    if re.search(r"TOTAL|GRAND|VAT|ภาษี|รวม", joined):
        return None
    if not code and not desc:
        return None
    if qty <= 0 and price <= 0 and amount <= 0:
        return None

    line = POLine(item_no=item, product_code_raw=code, description_raw=desc, qty=qty, price=price, amount=amount)
    line = repair_line_numbers(line)
    try:
        line.match_score = max([v for v in conf.values() if v >= 0] or [0])
    except Exception:
        pass
    return line


def _extract_footer_totals(image: Image.Image, table_bottom: int, lang: str, number_mode: str, filter_mode: str, remove_lines: bool) -> tuple[float | None, float | None, float | None]:
    w, h = image.size
    top = min(max(table_bottom, int(h * 0.55)), int(h * 0.84))
    crop = image.crop((int(w * 0.42), top, w, h))
    proc = _prepare_image(crop, filter_mode, remove_lines)
    try:
        words = extract_words(proc, lang="en", min_confidence=0.05)
        txt = words_to_text(words)
    except Exception:
        try:
            txt = pytesseract.image_to_string(enhance_for_ocr(crop, mode=filter_mode, remove_color_lines=remove_lines), lang=lang or "tha+eng", config="--oem 3 --psm 6")
        except Exception:
            txt = ""
    txt = normalize_digits(txt)
    vals: list[float] = []
    for token in re.findall(r"[0-9][0-9,\.]*", txt):
        v = parse_scanned_number(token, "amount", number_mode)
        if v is not None and 0 < v < 1_000_000_000:
            vals.append(round(v, 2))
    for i in range(0, max(0, len(vals) - 2)):
        t, vat, g = vals[i], vals[i + 1], vals[i + 2]
        if abs(vat - t * 0.07) <= max(2.0, t * 0.025) and abs(g - (t + vat)) <= max(2.0, g * 0.025):
            return t, vat, g
    if len(vals) >= 3:
        return vals[-3], vals[-2], vals[-1]
    return None, None, None


def build_po_document_template_v2(image: Image.Image, lang: str, template: dict | None, base_doc: PODocument | None = None) -> PODocument:
    doc = PODocument()
    profile = template or {}
    w, h = image.size
    boxes = _profile_boxes(profile, w, h)
    if not boxes:
        doc.warnings.append("ยังไม่มีกรอบสอนตำแหน่ง OCR ของลูกค้านี้")
        return doc

    number_mode = str(profile.get("number_mode") or "auto")
    filter_mode = str(profile.get("ocr_filter_mode") or profile.get("filter_mode") or "auto")
    remove_lines = bool(profile.get("remove_color_lines", True))
    proc_img = _prepare_image(image, filter_mode, remove_lines)

    try:
        words = extract_words(proc_img, lang="en", min_confidence=0.05)
    except Exception as exc:
        doc.warnings.append(f"PaddleOCR ใช้งานไม่ได้ จึง fallback บางช่องด้วย Tesseract: {exc}")
        words = []

    bands = _row_bands_from_words(words, boxes) if words else []
    if not bands:
        bands = _row_bands_from_grid(proc_img, boxes, filter_mode, remove_lines)

    lines: list[POLine] = []
    for band in bands:
        line = _line_from_row(proc_img, words, boxes, band, lang, number_mode, filter_mode, remove_lines)
        if line:
            lines.append(line)

    clean_lines: list[POLine] = []
    seen = set()
    for line in lines:
        key = (line.product_code_raw, line.description_raw, round(line.qty, 4), round(line.amount, 2))
        if key in seen:
            continue
        seen.add(key)
        clean_lines.append(line)
    for idx, line in enumerate(clean_lines, 1):
        if not str(line.item_no).strip() or not re.search(r"\d", str(line.item_no)):
            line.item_no = str(idx)

    doc.lines = clean_lines
    bottom = max((b[3] for b in boxes.values()), default=int(h * 0.75))
    printed_total, printed_vat, printed_grand = _extract_footer_totals(proc_img, bottom, lang, number_mode, filter_mode, remove_lines)
    line_total = round(sum((l.amount if l.amount > 0 else l.qty * l.price) for l in doc.lines), 2)
    if printed_total and printed_grand:
        doc.total = round(printed_total, 2)
        doc.vat = round(printed_vat if printed_vat is not None else printed_total * 0.07, 2)
        doc.grand_total = round(printed_grand, 2)
        if line_total and abs(line_total - doc.total) > max(1.0, doc.total * 0.02):
            doc.warnings.append(f"ยอดรายการ ({line_total:,.2f}) ยังไม่ตรงยอดท้ายบิล ({doc.total:,.2f})")
    else:
        doc.total = line_total
        doc.vat = round(doc.total * 0.07, 2)
        doc.grand_total = round(doc.total + doc.vat, 2)
        doc.warnings.append("อ่านยอดท้ายบิลไม่ชัด ระบบคำนวณยอดจากรายการแทน")

    if base_doc:
        doc.po_no = getattr(base_doc, "po_no", "") or doc.po_no
        doc.po_date = getattr(base_doc, "po_date", "") or doc.po_date
        doc.po_date_raw = getattr(base_doc, "po_date_raw", "") or doc.po_date_raw
    if not doc.lines:
        doc.warnings.append("PaddleOCR ไม่พบรายการสินค้า กรุณาตรวจกรอบสอนตำแหน่ง/Filter")
    doc._used_template = True  # type: ignore[attr-defined]
    doc._ocr_engine = "paddleocr"  # type: ignore[attr-defined]
    return doc
