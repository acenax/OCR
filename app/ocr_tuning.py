# -*- coding: utf-8 -*-
"""Extra OCR tuning helpers.

Goal:
- Do not OCR the whole page when a customer template exists.
- OCR each taught field with a matching whitelist/config.
- Clean coloured scan artefacts before OCR.
- Prefer footer totals when they can be read automatically.
- Repair qty / price / amount consistency.
"""
from __future__ import annotations

from pathlib import Path
import re
import math
from typing import Any, Dict, List, Tuple, Optional

import numpy as np
from PIL import Image

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None

try:
    import pytesseract
except Exception:  # pragma: no cover
    pytesseract = None

from .models import PODocument, POLine

_DIGIT_TRANS = str.maketrans({
    "๐": "0", "๑": "1", "๒": "2", "๓": "3", "๔": "4", "๕": "5", "๖": "6", "๗": "7", "๘": "8", "๙": "9",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4", "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
})

FIELD_KINDS = {"item": "number", "code": "code", "desc": "text", "qty": "qty", "price": "money", "amount": "money"}


def normalize_digits(text: Any) -> str:
    s = "" if text is None else str(text)
    return s.translate(_DIGIT_TRANS)


def normalize_text(text: Any) -> str:
    s = normalize_digits(text)
    s = s.replace("\u200b", " ").replace("\ufeff", " ")
    s = re.sub(r"[ \t\r\n]+", " ", s).strip()
    return s


def _numeric_candidates(text: Any) -> List[str]:
    s = normalize_digits(text)
    s = s.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
    return re.findall(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,4})?", s)


def _parse_normal_number(token: str) -> Optional[float]:
    try:
        return float(token.replace(",", ""))
    except Exception:
        return None


def parse_field_number(text: Any, field: str = "money") -> Optional[float]:
    cands = _numeric_candidates(text)
    if not cands:
        return None
    token = cands[-1]
    raw = token.replace(",", "")
    if "." in token or "," in token:
        return _parse_normal_number(token)
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    n = int(digits)
    if field == "qty":
        if len(digits) >= 4 and digits.endswith("00"):
            return n / 100.0
        return float(n)
    if field in {"price", "money", "amount"}:
        if len(digits) >= 5:
            return n / 100.0
        if len(digits) == 4 and digits.endswith("00"):
            return n / 100.0
        return float(n)
    return float(n)


def money(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        if isinstance(v, str):
            p = parse_field_number(v, "money")
            return float(p or 0.0)
        if math.isnan(float(v)):
            return 0.0
        return float(v)
    except Exception:
        return 0.0


def _remove_coloured_horizontal_lines(rgb: np.ndarray) -> np.ndarray:
    if cv2 is None:
        return rgb
    out = rgb.copy()
    hsv = cv2.cvtColor(out, cv2.COLOR_RGB2HSV)
    _h, s, v = cv2.split(hsv)
    colour_mask = ((s > 55) & (v > 70)).astype("uint8") * 255
    width = max(25, out.shape[1] // 18)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (width, 1))
    h_lines = cv2.morphologyEx(colour_mask, cv2.MORPH_OPEN, kernel)
    if h_lines.any():
        h_lines = cv2.dilate(h_lines, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)
        out[h_lines > 0] = (255, 255, 255)
    return out


def preprocess_crop(pil_img: Image.Image, kind: str = "text", scale: int = 3) -> Image.Image:
    if cv2 is None:
        return pil_img.convert("L")
    rgb = np.array(pil_img.convert("RGB"))
    rgb = _remove_coloured_horizontal_lines(rgb)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if scale and scale != 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    try:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
    except Exception:
        pass
    if kind in {"qty", "money", "number", "code"}:
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8), iterations=1)
        return Image.fromarray(th)
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 9)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((2, 1), np.uint8), iterations=1)
    return Image.fromarray(th)


def _field_config(field: str) -> Tuple[str, str]:
    kind = FIELD_KINDS.get(field, "text")
    if kind in {"qty", "money", "number"}:
        return "eng", "--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789.,-"
    if kind == "code":
        return "eng", "--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-./_"
    return "tha+eng", "--oem 3 --psm 6"


def _safe_ocr_data(img: Image.Image, field: str, lang: str) -> List[Dict[str, Any]]:
    if pytesseract is None or pd is None:
        return []
    ocr_lang, cfg = _field_config(field)
    if field == "desc":
        ocr_lang = lang or ocr_lang
    try:
        df = pytesseract.image_to_data(img, lang=ocr_lang, config=cfg, output_type=pytesseract.Output.DATAFRAME)
    except Exception:
        try:
            df = pytesseract.image_to_data(img, lang="eng", config=cfg, output_type=pytesseract.Output.DATAFRAME)
        except Exception:
            return []
    if df is None or df.empty or "text" not in df:
        return []
    df = df.dropna(subset=["text"])
    if df.empty:
        return []
    df["text"] = df["text"].astype(str).map(normalize_text)
    df = df[df["text"].str.strip() != ""]
    if df.empty:
        return []
    df["conf"] = pd.to_numeric(df.get("conf", -1), errors="coerce").fillna(-1)
    lines = []
    group_cols = [c for c in ["block_num", "par_num", "line_num"] if c in df.columns]
    if not group_cols:
        group_cols = ["top"]
    for _, g in df.sort_values(["top", "left"]).groupby(group_cols, sort=False):
        text = " ".join(g.sort_values("left")["text"].tolist()).strip()
        if not text:
            continue
        top = float(g["top"].min())
        bottom = float((g["top"] + g["height"]).max())
        left = float(g["left"].min())
        conf = float(g["conf"].mean()) if "conf" in g else -1.0
        lines.append({"text": text, "top": top, "bottom": bottom, "cy": (top + bottom) / 2.0, "left": left, "conf": conf})
    return lines


def _crop(pil_img: Image.Image, box: Tuple[float, float, float, float], pad: int = 3) -> Image.Image:
    w, h = pil_img.size
    l, t, r, b = box
    l = max(0, int(round(l)) - pad)
    t = max(0, int(round(t)) - pad)
    r = min(w, int(round(r)) + pad)
    b = min(h, int(round(b)) + pad)
    return pil_img.crop((l, t, r, b))


def _normalise_box(value: Any, w: int, h: int, fallback_top: float, fallback_bottom: float) -> Optional[Tuple[float, float, float, float]]:
    if not isinstance(value, (list, tuple)):
        return None
    vals = [float(x) for x in value]
    if len(vals) == 2:
        l, r = vals
        if max(abs(l), abs(r)) <= 1.5:
            l, r = l * w, r * w
        return (l, fallback_top, r, fallback_bottom)
    if len(vals) >= 4:
        l, t, r, b = vals[:4]
        if max(abs(l), abs(t), abs(r), abs(b)) <= 1.5:
            l, r = l * w, r * w
            t, b = t * h, b * h
        return (l, t, r, b)
    return None


def template_boxes(profile: Dict[str, Any], w: int, h: int) -> Dict[str, Tuple[float, float, float, float]]:
    cols = profile.get("columns") or profile.get("boxes") or {}
    if not isinstance(cols, dict):
        return {}
    fallback_top = float(profile.get("data_top_frac", 0.28) or 0.28) * h
    fallback_bottom = float(profile.get("bottom_frac", 0.78) or 0.78) * h
    boxes = {}
    for key, val in cols.items():
        box = _normalise_box(val, w, h, fallback_top, fallback_bottom)
        if box:
            l, t, r, b = box
            if r > l and b > t:
                boxes[str(key)] = (l, t, r, b)
    return boxes


def _ocr_lines_for_box(pil_img: Image.Image, box: Tuple[float, float, float, float], field: str, lang: str) -> List[Dict[str, Any]]:
    kind = FIELD_KINDS.get(field, "text")
    crop = _crop(pil_img, box, pad=4)
    proc = preprocess_crop(crop, kind=kind, scale=3)
    lines = _safe_ocr_data(proc, field, lang)
    scale = 3.0
    l, t, _, _ = box
    for line in lines:
        line["top"] = line["top"] / scale + t
        line["bottom"] = line["bottom"] / scale + t
        line["cy"] = line["cy"] / scale + t
        line["left"] = line["left"] / scale + l
    return lines


def _nearest(lines: List[Dict[str, Any]], y: float, tol: float) -> Optional[Dict[str, Any]]:
    if not lines:
        return None
    best = min(lines, key=lambda x: abs(float(x.get("cy", 0)) - y))
    if abs(float(best.get("cy", 0)) - y) <= tol:
        return best
    return None


def _line_number(line: Optional[Dict[str, Any]], field: str) -> Optional[float]:
    if not line:
        return None
    return parse_field_number(line.get("text", ""), field)


def _repair_line(line: POLine) -> POLine:
    q = money(line.qty)
    p = money(line.price)
    a = money(line.amount)
    if q > 0 and a > 0:
        inferred_p = a / q
        if p <= 0 or abs(q * p - a) > max(0.10, abs(a) * 0.03):
            if 0 < inferred_p < 10_000_000:
                p = inferred_p
    if q > 0 and p > 0:
        inferred_a = q * p
        if a <= 0 or abs(inferred_a - a) > max(0.10, abs(inferred_a) * 0.03):
            if a <= 0 or a < inferred_a * 0.5 or a > inferred_a * 2.0:
                a = inferred_a
    line.qty = round(q, 4)
    line.price = round(p, 4)
    line.amount = round(a, 2)
    line.item_no = normalize_text(line.item_no)
    line.product_code_raw = normalize_text(line.product_code_raw)
    line.description_raw = normalize_text(line.description_raw)
    return line


def _choose_footer_triple(values: List[float]) -> Tuple[float, float, float]:
    vals = [v for v in values if v and v > 0]
    if len(vals) < 3:
        return 0.0, 0.0, 0.0
    for i in range(len(vals) - 2):
        total, vat, grand = vals[i], vals[i + 1], vals[i + 2]
        if abs(total + vat - grand) <= max(2.0, total * 0.015) and abs(total * 0.07 - vat) <= max(2.0, total * 0.015):
            return round(total, 2), round(vat, 2), round(grand, 2)
    for total in vals:
        vat = min(vals, key=lambda x: abs(x - total * 0.07))
        grand = min(vals, key=lambda x: abs(x - (total + vat)))
        if grand > total and abs(total + vat - grand) <= max(2.0, total * 0.02):
            return round(total, 2), round(vat, 2), round(grand, 2)
    return 0.0, 0.0, 0.0


def extract_footer_totals(pil_img: Image.Image, boxes: Dict[str, Tuple[float, float, float, float]] | None = None, lang: str = "eng") -> Tuple[float, float, float]:
    w, h = pil_img.size
    if boxes and "amount" in boxes:
        l, _t, r, b = boxes["amount"]
        left = max(0, int(l - (r - l) * 1.8))
        top = min(h - 1, int(b + h * 0.015))
        region = (left, top, w, int(h * 0.94))
    else:
        region = (int(w * 0.58), int(h * 0.68), w, int(h * 0.94))
    crop = pil_img.crop(region)
    proc = preprocess_crop(crop, kind="money", scale=3)
    lines = _safe_ocr_data(proc, "amount", "eng")
    values: List[float] = []
    for ln in sorted(lines, key=lambda x: x.get("top", 0)):
        v = parse_field_number(ln.get("text", ""), "amount")
        if v and v > 0:
            values.append(float(v))
    return _choose_footer_triple(values)


def build_template_document(pil_img: Image.Image, lang: str, template: Dict[str, Any], base_doc: PODocument | None = None) -> Optional[PODocument]:
    if not template or not isinstance(template, dict):
        return None
    w, h = pil_img.size
    boxes = template_boxes(template, w, h)
    required = {"desc", "qty", "price", "amount"}
    if not required.issubset(set(boxes)):
        return None
    by_field: Dict[str, List[Dict[str, Any]]] = {field: _ocr_lines_for_box(pil_img, box, field, lang) for field, box in boxes.items()}
    anchors: List[float] = []
    for field in ("amount", "qty", "price", "code", "desc"):
        for ln in by_field.get(field, []):
            if field in {"amount", "qty", "price"} and _line_number(ln, field) is None:
                continue
            anchors.append(float(ln.get("cy", 0)))
        if len(anchors) >= 3:
            break
    if not anchors:
        return None
    anchors = sorted(anchors)
    merged: List[float] = []
    tol_merge = max(8.0, h * 0.008)
    for y in anchors:
        if not merged or abs(y - merged[-1]) > tol_merge:
            merged.append(y)
        else:
            merged[-1] = (merged[-1] + y) / 2.0
    tol = max(14.0, h * 0.018)
    lines: List[POLine] = []
    for idx, y in enumerate(merged, start=1):
        item_ln = _nearest(by_field.get("item", []), y, tol)
        code_ln = _nearest(by_field.get("code", []), y, tol)
        desc_ln = _nearest(by_field.get("desc", []), y, tol)
        qty_ln = _nearest(by_field.get("qty", []), y, tol)
        price_ln = _nearest(by_field.get("price", []), y, tol)
        amount_ln = _nearest(by_field.get("amount", []), y, tol)
        desc = normalize_text(desc_ln.get("text", "") if desc_ln else "")
        code = normalize_text(code_ln.get("text", "") if code_ln else "")
        if not desc and not code:
            continue
        q = _line_number(qty_ln, "qty") or 0.0
        p = _line_number(price_ln, "price") or 0.0
        a = _line_number(amount_ln, "amount") or 0.0
        item = normalize_text(item_ln.get("text", "") if item_ln else str(idx))
        lines.append(_repair_line(POLine(item_no=item, product_code_raw=code, description_raw=desc, qty=q, price=p, amount=a)))
    clean: List[POLine] = []
    for ln in lines:
        desc_low = normalize_text(ln.description_raw).lower()
        if any(x in desc_low for x in ["total", "grand", "vat", "รวม", "ภาษี"]):
            continue
        if (ln.description_raw or ln.product_code_raw) and (ln.qty or ln.price or ln.amount):
            clean.append(ln)
    if not clean:
        return None
    doc = PODocument()
    if base_doc:
        doc.source_pdf = base_doc.source_pdf
        doc.customer = base_doc.customer
        doc.po_no = base_doc.po_no
        doc.po_date = base_doc.po_date
        doc.po_date_raw = base_doc.po_date_raw
        doc.warnings = list(getattr(base_doc, "warnings", []) or [])
    doc.lines = clean
    total, vat, grand = extract_footer_totals(pil_img, boxes, lang)
    sum_amount = round(sum((l.amount or (l.qty * l.price)) for l in clean), 2)
    if total and vat and grand:
        doc.total, doc.vat, doc.grand_total = total, vat, grand
        if sum_amount and abs(sum_amount - total) > max(1.0, total * 0.02):
            doc.warnings.append(f"ยอดรายการจาก OCR ({sum_amount:,.2f}) ไม่ตรงยอดท้ายบิล ({total:,.2f}) — โปรดตรวจรายการ")
    else:
        doc.total = sum_amount
        doc.vat = round(sum_amount * 0.07, 2)
        doc.grand_total = round(doc.total + doc.vat, 2)
        doc.warnings.append("อ่านยอดท้ายบิลอัตโนมัติไม่สำเร็จ ใช้ยอดรวมจากรายการแทน")
    doc.warnings.append("ใช้ OCR แบบจูนแยกช่อง: ล้างเส้นสี + whitelist ตามชนิดข้อมูล")
    return doc


def tune_document(doc: PODocument, pil_img: Image.Image | None = None, lang: str = "tha+eng", template: Dict[str, Any] | None = None) -> PODocument:
    for ln in getattr(doc, "lines", []) or []:
        _repair_line(ln)
    if pil_img is not None:
        boxes = template_boxes(template or {}, *pil_img.size) if template else {}
        total, vat, grand = extract_footer_totals(pil_img, boxes, lang)
        if total and vat and grand:
            doc.total, doc.vat, doc.grand_total = total, vat, grand
        else:
            sum_amount = round(sum((money(l.amount) or money(l.qty) * money(l.price)) for l in getattr(doc, "lines", []) or []), 2)
            if sum_amount:
                doc.total = sum_amount
                doc.vat = round(sum_amount * 0.07, 2)
                doc.grand_total = round(doc.total + doc.vat, 2)
    return doc


def save_debug_snapshot(pil_img: Image.Image, name: str = "debug") -> str:
    try:
        d = Path.cwd() / "templates" / "_ocr_debug"
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"{name}.png"
        preprocess_crop(pil_img, kind="text", scale=2).save(out)
        return str(out)
    except Exception:
        return ""
