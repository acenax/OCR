from __future__ import annotations

import re
from typing import Any

import cv2
import numpy as np
import pandas as pd
import pytesseract
from PIL import Image

from .models import PODocument, POLine

ENGINE_VERSION = "stable_template_engine_v1"

_FIELD_ALIASES = {
    "item": {"item", "no", "seq", "ลำดับ", "no.", "item_no"},
    "code": {"code", "product_code", "product code", "รหัสสินค้า", "รหัสสินค้าฝั่งลูกค้า", "รหัสสินค้า (ocr)"},
    "desc": {"desc", "description", "name", "product", "ชื่อสินค้า", "รายการ", "ชื่อสินค้า/รายการ"},
    "qty": {"qty", "quantity", "จำนวน", "q'ty"},
    "price": {"price", "unit_price", "unit price", "ราคา", "ราคา/หน่วย", "หน่วยละ"},
    "amount": {"amount", "total", "line_total", "จำนวนเงิน", "ยอดเงิน", "ยอดเงิน ocr"},
}

_NUMERIC_FIELDS = {"item", "qty", "price", "amount"}

def normalize_digits(text: str) -> str:
    trans = str.maketrans({
        "๐": "0", "๑": "1", "๒": "2", "๓": "3", "๔": "4",
        "๕": "5", "๖": "6", "๗": "7", "๘": "8", "๙": "9",
        "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
        "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
        "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
        "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
        "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
        "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
        "O": "0", "o": "0", "I": "1", "l": "1", "|": "1",
    })
    return str(text or "").translate(trans)

def parse_num(text: str, field: str = "money") -> float | None:
    s = normalize_digits(text)
    s = s.replace(" ", "").replace("'", "").replace("`", "")
    s = s.replace("—", "-").replace("–", "-")
    s = re.sub(r"[^0-9,\.\-]", "", s)
    if not re.search(r"\d", s):
        return None

    last_dot = s.rfind(".")
    last_comma = s.rfind(",")
    dec_pos = max(last_dot, last_comma)
    if dec_pos >= 0:
        dec_digits = re.sub(r"\D", "", s[dec_pos+1:])
        # Treat the last separator as decimal only when it has 1-3 following digits.
        if len(dec_digits) in (1, 2, 3):
            int_part = re.sub(r"\D", "", s[:dec_pos]) or "0"
            if len(dec_digits) > 2 and field != "price":
                dec_digits = dec_digits[:2]
            try:
                return float(int_part + "." + dec_digits)
            except Exception:
                pass

    digits = re.sub(r"\D", "", s)
    if not digits:
        return None

    if field in {"qty", "price", "amount", "money"} and len(digits) > 2:
        return int(digits) / 100.0
    try:
        return float(digits)
    except Exception:
        return None

def fmt_text_for_code(text: str) -> str:
    s = normalize_digits(text).strip()
    s = re.sub(r"\s+", "", s)
    s = s.replace("—", "-").replace("–", "-")
    return s

def enhance_for_ocr(pil_img: Image.Image, numeric: bool = False) -> Image.Image:
    img = pil_img.convert("RGB")
    arr = np.array(img)

    # Remove colored horizontal noise lines (red/green/blue scanner streaks).
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    color_mask = ((sat > 60) & (val > 80)).astype(np.uint8) * 255
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, arr.shape[1] // 12), 1))
    lines = cv2.morphologyEx(color_mask, cv2.MORPH_OPEN, h_kernel, iterations=1)
    arr[lines > 0] = [255, 255, 255]

    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    gray = cv2.fastNlMeansDenoising(gray, None, 8, 7, 21)
    gray = cv2.equalizeHist(gray)
    scale = 2.2 if numeric else 1.8
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    th = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11
    )
    return Image.fromarray(th)

def tesseract_text(pil_img: Image.Image, field: str) -> tuple[str, float]:
    numeric = field in {"item", "qty", "price", "amount", "money", "po_no", "date"}
    proc = enhance_for_ocr(pil_img, numeric=numeric)
    if field in {"qty", "price", "amount", "money"}:
        config = "--psm 6 -c tessedit_char_whitelist=0123456789.,-"
        lang = "eng"
    elif field in {"item"}:
        config = "--psm 6 -c tessedit_char_whitelist=0123456789."
        lang = "eng"
    elif field == "code":
        config = "--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-./"
        lang = "eng"
    else:
        config = "--psm 6"
        lang = "tha+eng"

    try:
        data = pytesseract.image_to_data(proc, lang=lang, config=config, output_type=pytesseract.Output.DATAFRAME)
    except Exception:
        data = pytesseract.image_to_data(proc, lang="eng", config=config, output_type=pytesseract.Output.DATAFRAME)

    data = data.dropna(subset=["text"])
    data["text"] = data["text"].astype(str)
    data = data[data["text"].str.strip() != ""]
    if data.empty:
        return "", 0.0
    conf = pd.to_numeric(data["conf"], errors="coerce")
    good = data[conf.fillna(-1) >= 0]
    text = " ".join(good["text"].astype(str).tolist()) if not good.empty else " ".join(data["text"].astype(str).tolist())
    cval = float(conf[conf >= 0].mean()) if (conf >= 0).any() else 0.0
    return text.strip(), cval

def tesseract_lines(pil_img: Image.Image, field: str, page_box: tuple[int, int, int, int]) -> list[dict[str, Any]]:
    numeric = field in _NUMERIC_FIELDS
    proc = enhance_for_ocr(pil_img, numeric=numeric)
    if field in {"qty", "price", "amount"}:
        config = "--psm 6 -c tessedit_char_whitelist=0123456789.,-"
        lang = "eng"
    elif field == "item":
        config = "--psm 6 -c tessedit_char_whitelist=0123456789."
        lang = "eng"
    elif field == "code":
        config = "--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-./"
        lang = "eng"
    else:
        config = "--psm 6"
        lang = "tha+eng"

    try:
        df = pytesseract.image_to_data(proc, lang=lang, config=config, output_type=pytesseract.Output.DATAFRAME)
    except Exception:
        df = pytesseract.image_to_data(proc, lang="eng", config=config, output_type=pytesseract.Output.DATAFRAME)
    df = df.dropna(subset=["text"])
    if df.empty:
        return []
    df["text"] = df["text"].astype(str)
    df = df[df["text"].str.strip() != ""]
    if df.empty:
        return []
    df["conf"] = pd.to_numeric(df["conf"], errors="coerce").fillna(-1)

    x1, y1, x2, y2 = page_box
    scale_x = (x2 - x1) / max(1, proc.size[0])
    scale_y = (y2 - y1) / max(1, proc.size[1])

    out = []
    for _, g in df.groupby(["block_num", "par_num", "line_num"]):
        g = g.sort_values("left")
        txt = " ".join(g["text"].astype(str).tolist()).strip()
        if not txt:
            continue
        top = float(g["top"].min())
        bottom = float((g["top"] + g["height"]).max())
        cy = y1 + ((top + bottom) / 2.0) * scale_y
        conf = float(g[g["conf"] >= 0]["conf"].mean()) if (g["conf"] >= 0).any() else 0.0
        out.append({"field": field, "text": txt, "cy": cy, "conf": conf})
    return out

def canonical_field(name: str) -> str | None:
    key = str(name or "").strip().lower()
    key = key.replace("_raw", "").replace("(ocr)", "").strip()
    for canon, aliases in _FIELD_ALIASES.items():
        if key == canon or key in aliases:
            return canon
    if "amount" in key or "จำนวนเงิน" in key or "ยอดเงิน" in key:
        return "amount"
    if "price" in key or "ราคา" in key or "หน่วยละ" in key:
        return "price"
    if "qty" in key or "จำนวน" in key:
        return "qty"
    if "description" in key or "desc" in key or "รายการ" in key or "ชื่อสินค้า" in key:
        return "desc"
    if "code" in key or "รหัส" in key:
        return "code"
    if "item" in key or "ลำดับ" in key:
        return "item"
    return None

def _box_tuple(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, dict):
        keys = {str(k).lower(): v for k, v in value.items()}
        if all(k in keys for k in ("left", "top", "right", "bottom")):
            return float(keys["left"]), float(keys["top"]), float(keys["right"]), float(keys["bottom"])
        if all(k in keys for k in ("x1", "y1", "x2", "y2")):
            return float(keys["x1"]), float(keys["y1"]), float(keys["x2"]), float(keys["y2"])
        if all(k in keys for k in ("l", "t", "r", "b")):
            return float(keys["l"]), float(keys["t"]), float(keys["r"]), float(keys["b"])
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return tuple(float(x) for x in value)  # type: ignore[return-value]
    return None

def extract_boxes(profile: dict | None) -> dict[str, tuple[float, float, float, float]]:
    if not profile:
        return {}
    raw_sources = []
    for key in ("boxes", "column_boxes", "fields", "field_boxes", "columns"):
        if isinstance(profile.get(key), dict):
            raw_sources.append(profile[key])

    boxes: dict[str, tuple[float, float, float, float]] = {}
    for src in raw_sources:
        for k, v in src.items():
            canon = canonical_field(k)
            if not canon:
                continue
            b = _box_tuple(v)
            if not b:
                continue
            boxes[canon] = b
    return boxes

def normalize_box(box: tuple[float, float, float, float], W: int, H: int) -> tuple[int, int, int, int]:
    l, t, r, b = box
    if max(abs(l), abs(t), abs(r), abs(b)) <= 1.5:
        x1, y1, x2, y2 = int(l * W), int(t * H), int(r * W), int(b * H)
    else:
        x1, y1, x2, y2 = int(l), int(t), int(r), int(b)
    x1 = max(0, min(W - 1, x1))
    y1 = max(0, min(H - 1, y1))
    x2 = max(x1 + 2, min(W, x2))
    y2 = max(y1 + 2, min(H, y2))
    return x1, y1, x2, y2

def crop_box(pil_img: Image.Image, box_px: tuple[int, int, int, int], pad: int = 2) -> Image.Image:
    W, H = pil_img.size
    x1, y1, x2, y2 = box_px
    return pil_img.crop((max(0, x1-pad), max(0, y1-pad), min(W, x2+pad), min(H, y2+pad)))

def group_y_centers(entries: list[dict[str, Any]]) -> list[float]:
    ys = sorted([float(e["cy"]) for e in entries])
    if not ys:
        return []
    diffs = [ys[i+1] - ys[i] for i in range(len(ys)-1) if ys[i+1] - ys[i] > 2]
    med = np.median(diffs) if diffs else 24
    tol = max(10, min(28, med * 0.45))
    groups = []
    cur = [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] <= tol:
            cur.append(y)
        else:
            groups.append(sum(cur) / len(cur))
            cur = [y]
    groups.append(sum(cur) / len(cur))
    return groups

def nearest_entry(entries: list[dict[str, Any]], cy: float, tol: float) -> dict[str, Any] | None:
    if not entries:
        return None
    best = min(entries, key=lambda e: abs(float(e["cy"]) - cy))
    if abs(float(best["cy"]) - cy) <= tol:
        return best
    return None

def repair_line_numbers(qty: float | None, price: float | None, amount: float | None) -> tuple[float, float, float, list[str]]:
    q = float(qty or 0)
    p = float(price or 0)
    a = float(amount or 0)
    notes = []

    if q > 0 and a > 0:
        derived_p = round(a / q, 4)
        if p <= 0 or abs((q * p) - a) > max(0.5, a * 0.03):
            if 0 < derived_p < 1_000_000:
                p = derived_p
                notes.append("price_repaired_from_amount")
    if q > 0 and p > 0:
        derived_a = round(q * p, 2)
        if a <= 0 or abs(derived_a - a) > max(0.5, derived_a * 0.03):
            if a <= 0 or abs(derived_a - a) <= max(5.0, derived_a * 0.15):
                a = derived_a
                notes.append("amount_repaired_from_qty_price")
    return round(q, 4), round(p, 4), round(a, 2), notes

def extract_footer_totals(pil_img: Image.Image) -> tuple[float | None, float | None, float | None]:
    W, H = pil_img.size
    roi = pil_img.crop((int(W * 0.52), int(H * 0.68), W, int(H * 0.93)))
    text, _ = tesseract_text(roi, "money")
    vals = []
    for m in re.finditer(r"\d[\d,\.]{2,}", normalize_digits(text)):
        v = parse_num(m.group(0), "amount")
        if v and 1 <= v <= 100_000_000:
            vals.append(v)
    clean = []
    for v in vals:
        if all(abs(v - x) > 0.01 for x in clean):
            clean.append(v)
    vals = clean
    if len(vals) >= 3:
        best = None
        best_score = 1e18
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                for k in range(j + 1, len(vals)):
                    total, vat, grand = vals[i], vals[j], vals[k]
                    score = abs(vat - round(total * 0.07, 2)) + abs(grand - round(total + vat, 2))
                    if score < best_score:
                        best_score = score
                        best = (total, vat, grand)
        if best and best_score <= max(3.0, best[0] * 0.01):
            return best
        return vals[-3], vals[-2], vals[-1]
    return None, None, None

def build_po_document(pil_img: Image.Image, lang: str, template: dict | None = None) -> PODocument:
    doc = PODocument()
    W, H = pil_img.size
    boxes_norm = extract_boxes(template)
    required = {"desc", "qty", "price", "amount"}
    if not required.issubset(set(boxes_norm.keys())):
        doc.warnings.append("stable_template_engine: ยังไม่มีกรอบสอนตำแหน่งครบ จึง fallback")
        return doc

    boxes = {k: normalize_box(v, W, H) for k, v in boxes_norm.items()}

    field_entries: dict[str, list[dict[str, Any]]] = {}
    all_entries_for_rows = []
    for field, box in boxes.items():
        im = crop_box(pil_img, box)
        lines = tesseract_lines(im, field, box)
        field_entries[field] = lines
        if field in {"item", "code", "qty", "amount"}:
            all_entries_for_rows.extend(lines)

    centers = group_y_centers(all_entries_for_rows)
    if not centers:
        doc.warnings.append("stable_template_engine: หาแถวสินค้าไม่เจอจากกรอบที่สอน")
        return doc

    if len(centers) > 1:
        diffs = [centers[i+1] - centers[i] for i in range(len(centers)-1)]
        tol = max(12, min(35, float(np.median(diffs)) * 0.45))
    else:
        tol = 20.0

    out_lines: list[POLine] = []
    for row_idx, cy in enumerate(centers, 1):
        raw: dict[str, str] = {}
        for field in ("item", "code", "desc", "qty", "price", "amount"):
            ent = nearest_entry(field_entries.get(field, []), cy, tol)
            raw[field] = ent["text"] if ent else ""

        code = fmt_text_for_code(raw.get("code", ""))
        desc = re.sub(r"\s+", " ", raw.get("desc", "")).strip()
        item_val = parse_num(raw.get("item", ""), "item")
        item = str(int(item_val)) if item_val is not None else str(row_idx)
        qty = parse_num(raw.get("qty", ""), "qty")
        price = parse_num(raw.get("price", ""), "price")
        amount = parse_num(raw.get("amount", ""), "amount")
        q, p, a, notes = repair_line_numbers(qty, price, amount)

        if not any([code, desc, q, p, a]):
            continue
        desc_up = desc.upper()
        if any(x in desc_up for x in ("TOTAL", "GRAND", "VAT", "รวมราคา", "รวมเงิน")):
            continue

        line = POLine(
            item_no=item,
            product_code_raw=code,
            description_raw=desc,
            qty=q,
            price=p,
            amount=a,
        )
        if notes:
            line.match_status = "review"
        out_lines.append(line)

    filtered = []
    for ln in out_lines:
        if not (ln.product_code_raw or ln.description_raw):
            continue
        if ln.qty == 0 and ln.price == 0 and ln.amount == 0:
            continue
        filtered.append(ln)
    doc.lines = filtered

    printed_total, printed_vat, printed_grand = extract_footer_totals(pil_img)
    line_total = round(sum((l.amount if l.amount else l.qty * l.price) for l in doc.lines), 2)

    if printed_total and printed_grand:
        doc.total = round(printed_total, 2)
        doc.vat = round(printed_vat if printed_vat is not None else printed_total * 0.07, 2)
        doc.grand_total = round(printed_grand, 2)
        if line_total and abs(line_total - doc.total) > max(2.0, doc.total * 0.01):
            doc.warnings.append(
                f"ยอดรายการ ({line_total:,.2f}) ไม่ตรงยอดท้ายบิล ({doc.total:,.2f}) — ให้ตรวจแถวสีเหลือง/แดง"
            )
    else:
        doc.total = line_total
        doc.vat = round(doc.total * 0.07, 2)
        doc.grand_total = round(doc.total + doc.vat, 2)
        doc.warnings.append("อ่านยอดท้ายบิลไม่ชัด จึงคำนวณยอดจากรายการแทน")

    doc.warnings.append(f"OCR engine: {ENGINE_VERSION}")
    return doc
