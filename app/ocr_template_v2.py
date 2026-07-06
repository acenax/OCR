
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
import pytesseract
from PIL import Image

from .models import PODocument, POLine

FIELD_ALIASES = {
    "item": {"item", "ลำดับ", "seq", "no", "no.", "number"},
    "code": {"code", "product_code", "product code", "รหัสสินค้า", "รหัสสินค้าฝั่งลูกค้า", "customer_code"},
    "desc": {"desc", "description", "name", "product_name", "ชื่อสินค้า", "รายการ", "ชื่อสินค้า/รายการ"},
    "qty": {"qty", "quantity", "จำนวน", "q'ty", "q ty"},
    "price": {"price", "unit_price", "unit price", "ราคา", "ราคา/หน่วย", "หน่วยละ"},
    "amount": {"amount", "total", "line_total", "จำนวนเงิน", "ยอดเงิน", "ยอดเงิน ocr"},
}

NUMERIC_FIELDS = {"qty", "price", "amount"}


def _canon_field(name: str) -> str:
    raw = str(name or "").strip().lower().replace("_", " ")
    for key, aliases in FIELD_ALIASES.items():
        if raw in aliases:
            return key
    # fuzzy contains fallback
    if "amount" in raw or "จำนวนเงิน" in raw or "ยอดเงิน" in raw:
        return "amount"
    if "price" in raw or "ราคา" in raw or "หน่วยละ" in raw:
        return "price"
    if "qty" in raw or "quantity" in raw or "จำนวน" in raw:
        return "qty"
    if "description" in raw or "รายการ" in raw or "ชื่อสินค้า" in raw:
        return "desc"
    if "code" in raw or "รหัส" in raw:
        return "code"
    if "item" in raw or "ลำดับ" in raw:
        return "item"
    return raw


def _to_float_box(value: Any) -> list[float] | None:
    """Accept multiple template formats and return [l,t,r,b] or None."""
    if value is None:
        return None
    if isinstance(value, dict):
        keys = {k.lower(): k for k in value.keys()}
        def pick(*names, default=None):
            for n in names:
                if n in keys:
                    return value[keys[n]]
            return default
        l = pick("left", "x", "x1", "lo")
        t = pick("top", "y", "y1", default=None)
        r = pick("right", "x2", "hi")
        b = pick("bottom", "y2", default=None)
        w = pick("width", "w", default=None)
        h = pick("height", "h", default=None)
        if r is None and l is not None and w is not None:
            r = float(l) + float(w)
        if b is None and t is not None and h is not None:
            b = float(t) + float(h)
        if l is not None and r is not None:
            if t is None:
                t = 0.0
            if b is None:
                b = 1.0
            try:
                return [float(l), float(t), float(r), float(b)]
            except Exception:
                return None
    if isinstance(value, (list, tuple)):
        try:
            vals = [float(x) for x in value]
        except Exception:
            return None
        if len(vals) >= 4:
            l, t, r, b = vals[:4]
            return [l, t, r, b]
        if len(vals) == 2:
            # old format: [x_left_frac, x_right_frac], y is supplied separately
            l, r = vals
            return [l, 0.0, r, 1.0]
    return None


def _collect_raw_boxes(profile: dict) -> dict[str, list[float]]:
    boxes: dict[str, list[float]] = {}
    for key_name in ("boxes", "field_boxes", "columns_full", "column_boxes", "rects", "fields"):
        obj = profile.get(key_name)
        if isinstance(obj, dict):
            for k, v in obj.items():
                f = _canon_field(k)
                box = _to_float_box(v)
                if f and box:
                    boxes[f] = box
    cols = profile.get("columns")
    if isinstance(cols, dict):
        for k, v in cols.items():
            f = _canon_field(k)
            box = _to_float_box(v)
            if f and box:
                boxes.setdefault(f, box)
    return boxes


def _profile_image_size(profile: dict) -> tuple[float | None, float | None]:
    keys_w = ["image_width", "page_width", "pixmap_width", "snapshot_width", "teaching_width", "width"]
    keys_h = ["image_height", "page_height", "pixmap_height", "snapshot_height", "teaching_height", "height"]
    w = h = None
    for k in keys_w:
        if k in profile:
            try:
                w = float(profile[k])
                break
            except Exception:
                pass
    for k in keys_h:
        if k in profile:
            try:
                h = float(profile[k])
                break
            except Exception:
                pass
    return w, h


def _normalize_boxes(profile: dict, W: int, H: int) -> dict[str, tuple[int, int, int, int]]:
    raw = _collect_raw_boxes(profile)
    if not raw:
        return {}
    prof_w, prof_h = _profile_image_size(profile)
    # y range for old x-only columns
    data_top = float(profile.get("data_top_frac", 0.0) or 0.0)
    bottom = float(profile.get("bottom_frac", 1.0) or 1.0)

    max_val = max(max(abs(x) for x in box) for box in raw.values())
    looks_normalized = max_val <= 1.5
    sx = sy = 1.0
    if not looks_normalized and prof_w and prof_h and prof_w > 10 and prof_h > 10:
        sx, sy = W / prof_w, H / prof_h
    elif not looks_normalized:
        # Heuristic for old teaching coordinates saved from the visible image, not the render size.
        # If all boxes occupy a narrow apparent page, scale them to current image width/height.
        max_r = max(b[2] for b in raw.values())
        max_b = max(b[3] for b in raw.values())
        min_l = min(b[0] for b in raw.values())
        min_t = min(b[1] for b in raw.values())
        if 100 < max_r < W * 0.75:
            sx = W / max(max_r + min_l, max_r)
        if 100 < max_b < H * 0.75:
            sy = H / max(max_b + min_t, max_b)

    out: dict[str, tuple[int, int, int, int]] = {}
    for f, box in raw.items():
        l, t, r, b = box
        # old columns often store only x fractions with y=0,b=1
        if f in raw and abs(t) < 1e-9 and abs(b - 1.0) < 1e-9:
            t = data_top
            b = bottom
        if looks_normalized:
            l, r = l * W, r * W
            t, b = t * H, b * H
        else:
            l, r = l * sx, r * sx
            t, b = t * sy, b * sy
        x1, x2 = sorted((int(round(l)), int(round(r))))
        y1, y2 = sorted((int(round(t)), int(round(b))))
        pad_x = max(2, int((x2 - x1) * 0.03))
        pad_y = max(2, int((y2 - y1) * 0.01))
        x1 = max(0, x1 - pad_x)
        x2 = min(W, x2 + pad_x)
        y1 = max(0, y1 - pad_y)
        y2 = min(H, y2 + pad_y)
        if x2 - x1 > 5 and y2 - y1 > 5:
            out[f] = (x1, y1, x2, y2)
    return out


def _remove_colored_lines(rgb: np.ndarray) -> np.ndarray:
    """Remove red/green/blue scanner artifacts while keeping dark text."""
    out = rgb.copy()
    hsv = cv2.cvtColor(out, cv2.COLOR_RGB2HSV)
    # high saturation, medium/high value = colored line/noise
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    mask = (sat > 70) & (val > 70)
    # preserve very dark pixels because those are likely text/table strokes
    gray = cv2.cvtColor(out, cv2.COLOR_RGB2GRAY)
    mask &= gray > 80
    out[mask] = [255, 255, 255]
    return out


def _prep_for_ocr(pil: Image.Image, numeric: bool = False, scale: int = 3) -> Image.Image:
    rgb = np.array(pil.convert("RGB"))
    rgb = _remove_colored_lines(rgb)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    # contrast boost
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    if numeric:
        # numbers are faint: enlarge first, then threshold
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 31, 12)
    else:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 35, 11)
    return Image.fromarray(th)


def _ocr_text(pil: Image.Image, lang: str, field: str, psm: int = 7) -> str:
    numeric = field in NUMERIC_FIELDS or field == "item"
    img = _prep_for_ocr(pil, numeric=numeric, scale=4 if numeric else 3)
    if field in ("qty", "price", "amount"):
        cfg = f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789.,-"
        use_lang = "eng"
    elif field == "item":
        cfg = f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789"
        use_lang = "eng"
    elif field == "code":
        cfg = f"--oem 3 --psm {psm} -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-./"
        use_lang = "eng"
    else:
        cfg = f"--oem 3 --psm {psm}"
        use_lang = lang or "tha+eng"
    try:
        txt = pytesseract.image_to_string(img, lang=use_lang, config=cfg)
    except Exception:
        txt = pytesseract.image_to_string(img, lang="eng", config=cfg)
    return _clean_text(txt)


def _clean_text(s: str) -> str:
    s = str(s or "").replace("\x0c", " ")
    s = re.sub(r"[\r\n\t]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_number(text: str, field: str) -> float | None:
    s = str(text or "")
    s = s.translate(str.maketrans("๐๑๒๓๔๕๖๗๘๙٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹０１２３４５６７８９", "0123456789012345678901234567890123456789"))
    s = s.replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")
    s = re.sub(r"[^0-9,\.\-]", "", s)
    if not s:
        return None
    # pick the longest number-looking token
    toks = re.findall(r"-?[0-9][0-9,]*(?:\.[0-9]+)?", s)
    token = max(toks, key=len) if toks else s
    token = token.replace(",", "")
    try:
        return float(token)
    except Exception:
        pass
    digits = re.sub(r"\D", "", token)
    if not digits:
        return None
    val = float(int(digits))
    if field in ("qty", "price", "amount") and len(digits) >= 3:
        # dot-matrix fallback when decimal point is lost
        return val / 100.0
    return val


def _horizontal_text_centers(pil_img: Image.Image, boxes: dict[str, tuple[int, int, int, int]]) -> list[float]:
    # Use description/code area for row detection. Numeric columns are too sparse.
    src_fields = [f for f in ("desc", "code", "qty", "amount") if f in boxes]
    if not src_fields:
        return []
    x1 = min(boxes[f][0] for f in src_fields)
    y1 = min(boxes[f][1] for f in src_fields)
    x2 = max(boxes[f][2] for f in src_fields)
    y2 = max(boxes[f][3] for f in src_fields)
    crop = pil_img.crop((x1, y1, x2, y2))
    rgb = np.array(crop.convert("RGB"))
    rgb = _remove_colored_lines(rgb)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 35, 12)
    # remove long vertical/horizontal table lines from projection
    h, w = th.shape
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, h // 5)))
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 3), 1))
    lines = cv2.morphologyEx(th, cv2.MORPH_OPEN, vk) | cv2.morphologyEx(th, cv2.MORPH_OPEN, hk)
    text = cv2.subtract(th, lines)
    proj = text.sum(axis=1) / 255.0
    threshold = max(3.0, w * 0.008)
    idx = np.where(proj > threshold)[0]
    if idx.size == 0:
        return []
    groups = []
    cur = [int(idx[0])]
    for yy in idx[1:]:
        yy = int(yy)
        if yy - cur[-1] <= 5:
            cur.append(yy)
        else:
            groups.append(cur)
            cur = [yy]
    groups.append(cur)
    centers = []
    for g in groups:
        height = g[-1] - g[0] + 1
        if 3 <= height <= max(60, h * 0.20):
            centers.append(y1 + (g[0] + g[-1]) / 2.0)
    # Merge very close centers
    centers = sorted(centers)
    merged = []
    for c in centers:
        if not merged or c - merged[-1] > max(10, (y2 - y1) * 0.025):
            merged.append(c)
        else:
            merged[-1] = (merged[-1] + c) / 2.0
    return merged


def _item_centers_from_ocr(pil_img: Image.Image, boxes: dict[str, tuple[int, int, int, int]], lang: str) -> list[float]:
    if "item" not in boxes:
        return []
    x1, y1, x2, y2 = boxes["item"]
    crop = pil_img.crop((x1, y1, x2, y2))
    img = _prep_for_ocr(crop, numeric=True, scale=4)
    try:
        data = pytesseract.image_to_data(img, lang="eng", config="--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789", output_type=pytesseract.Output.DICT)
    except Exception:
        return []
    centers = []
    scale_y = (y2 - y1) / max(1, img.height)
    for txt, top, hh, conf in zip(data.get("text", []), data.get("top", []), data.get("height", []), data.get("conf", [])):
        if not str(txt).strip().isdigit():
            continue
        try:
            n = int(str(txt).strip())
        except Exception:
            continue
        if 1 <= n <= 200:
            centers.append(y1 + (float(top) + float(hh) / 2.0) * scale_y)
    return sorted(centers)


def _row_bounds_from_centers(centers: list[float], y_top: int, y_bottom: int) -> list[tuple[int, int, float]]:
    centers = sorted([c for c in centers if y_top <= c <= y_bottom])
    if not centers:
        return []
    mids = [(centers[i] + centers[i + 1]) / 2.0 for i in range(len(centers) - 1)]
    bounds = []
    for i, c in enumerate(centers):
        top = y_top if i == 0 else mids[i - 1]
        bot = y_bottom if i == len(centers) - 1 else mids[i]
        pad = max(2, int((bot - top) * 0.12))
        bounds.append((max(y_top, int(top) - pad), min(y_bottom, int(bot) + pad), c))
    return bounds


def _repair_numbers(qty: float | None, price: float | None, amount: float | None) -> tuple[float, float, float]:
    q = float(qty or 0)
    p = float(price or 0)
    a = float(amount or 0)
    # if amount and qty are reliable, price should come from amount / qty
    if q > 0 and a > 0:
        calc_p = round(a / q, 4)
        if p <= 0 or abs(q * p - a) > max(0.03, abs(a) * 0.03):
            p = calc_p
    if q > 0 and p > 0:
        calc_a = round(q * p, 2)
        if a <= 0 or abs(calc_a - a) > max(0.03, abs(calc_a) * 0.03):
            a = calc_a
    return q, p, a


def _read_footer_totals(pil_img: Image.Image, lang: str) -> tuple[float | None, float | None, float | None]:
    W, H = pil_img.size
    # bottom-right region where CMT prints TOTAL/VAT/GRAND TOTAL
    region = pil_img.crop((int(W * 0.55), int(H * 0.62), int(W * 0.98), int(H * 0.93)))
    img = _prep_for_ocr(region, numeric=False, scale=3)
    try:
        txt = pytesseract.image_to_string(img, lang="eng", config="--oem 3 --psm 6")
    except Exception:
        txt = ""
    nums = []
    for raw in re.findall(r"[0-9][0-9,]*(?:\.[0-9]{1,2})?", txt):
        v = _parse_number(raw, "amount")
        if v and v > 0:
            nums.append(v)
    # Prefer the final 3 money values in footer. Filter out tiny date/form numbers.
    nums = [n for n in nums if n >= 1]
    if len(nums) >= 3:
        return nums[-3], nums[-2], nums[-1]
    return None, None, None


def build_po_document_template_v2(pil_img: Image.Image, lang: str, template: dict | None = None) -> PODocument:
    doc = PODocument()
    profile = template or {}
    W, H = pil_img.size
    boxes = _normalize_boxes(profile, W, H)
    required = {"code", "desc", "qty", "price", "amount"}
    if not required.intersection(boxes.keys()):
        doc.warnings.append("Template OCR v2: ยังไม่มีกรอบสอนตำแหน่งที่ใช้ได้")
        return doc

    y_top = min(b[1] for b in boxes.values())
    y_bottom = max(b[3] for b in boxes.values())
    # row centers from item column first, then projection fallback
    centers = _item_centers_from_ocr(pil_img, boxes, lang)
    if len(centers) < 2:
        centers = _horizontal_text_centers(pil_img, boxes)
    bounds = _row_bounds_from_centers(centers, y_top, y_bottom)
    if not bounds:
        doc.warnings.append("Template OCR v2: หาแนวแถวสินค้าไม่เจอ")
        return doc

    lines: list[POLine] = []
    for idx, (rtop, rbot, center) in enumerate(bounds, start=1):
        def crop_field(field: str):
            if field not in boxes:
                return None
            x1, y1, x2, y2 = boxes[field]
            return pil_img.crop((x1, max(y1, rtop), x2, min(y2, rbot)))

        item_txt = _ocr_text(crop_field("item"), lang, "item") if crop_field("item") else str(idx)
        code_txt = _ocr_text(crop_field("code"), lang, "code") if crop_field("code") else ""
        desc_txt = _ocr_text(crop_field("desc"), lang, "desc") if crop_field("desc") else ""
        qty_txt = _ocr_text(crop_field("qty"), lang, "qty") if crop_field("qty") else ""
        price_txt = _ocr_text(crop_field("price"), lang, "price") if crop_field("price") else ""
        amount_txt = _ocr_text(crop_field("amount"), lang, "amount") if crop_field("amount") else ""
        qty = _parse_number(qty_txt, "qty")
        price = _parse_number(price_txt, "price")
        amount = _parse_number(amount_txt, "amount")
        q, p, a = _repair_numbers(qty, price, amount)
        # Skip empty/noise rows. Keep rows with a code/description or numeric amount.
        if not code_txt and not desc_txt and q <= 0 and a <= 0:
            continue
        # Filter probable footer row if it slipped into the bottom of body.
        if re.search(r"TOTAL|VAT|GRAND|รวม", (code_txt + " " + desc_txt).upper()) and not code_txt.strip():
            continue
        item_no = re.sub(r"\D+", "", item_txt) or str(idx)
        lines.append(POLine(
            item_no=item_no,
            product_code_raw=code_txt.strip(),
            description_raw=desc_txt.strip(),
            qty=q,
            price=p,
            amount=a,
        ))

    doc.lines = lines
    total, vat, grand = _read_footer_totals(pil_img, lang)
    sum_amount = round(sum(l.amount if l.amount else l.qty * l.price for l in doc.lines), 2)
    if total and grand:
        doc.total = round(total, 2)
        doc.vat = round(vat if vat is not None else max(0, grand - total), 2)
        doc.grand_total = round(grand, 2)
        if sum_amount and abs(sum_amount - doc.total) > max(1.0, doc.total * 0.03):
            doc.warnings.append(f"ยอดรายการ {sum_amount:,.2f} ไม่ตรงยอดท้ายบิล {doc.total:,.2f} โปรดตรวจ")
    else:
        doc.total = sum_amount
        doc.vat = round(doc.total * 0.07, 2)
        doc.grand_total = round(doc.total + doc.vat, 2)
        doc.warnings.append("อ่านยอดท้ายบิลไม่ได้ จึงคำนวณจากรายการสินค้า")
    doc._used_template_v2 = True  # type: ignore[attr-defined]
    return doc


# === OCR FILTER PROFILE PATCH ===
try:
    from .ocr_image_filters import apply_ocr_filter, remove_colored_artifacts
    _ocr_filter_active_profile = "auto"
    _ocr_filter_remove_lines = True

    def _set_ocr_filter_profile(template=None):
        global _ocr_filter_active_profile, _ocr_filter_remove_lines
        if isinstance(template, dict):
            _ocr_filter_active_profile = str(template.get("ocr_filter_profile") or "auto")
            _ocr_filter_remove_lines = bool(template.get("ocr_remove_colored_lines", True))
        else:
            _ocr_filter_active_profile = "auto"
            _ocr_filter_remove_lines = True

    def _remove_colored_lines(rgb):  # type: ignore[no-redef]
        return remove_colored_artifacts(rgb, aggressive=_ocr_filter_remove_lines)

    def _prep_for_ocr(pil, numeric=False, scale=3):  # type: ignore[no-redef]
        import cv2
        import numpy as np
        from PIL import Image
        base = apply_ocr_filter(
            pil,
            profile=_ocr_filter_active_profile,
            numeric=bool(numeric),
            remove_lines=_ocr_filter_remove_lines,
            threshold=False,
        )
        gray = cv2.cvtColor(np.array(base.convert("RGB")), cv2.COLOR_RGB2GRAY)
        if scale and scale != 1:
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        if numeric:
            gray = cv2.GaussianBlur(gray, (3, 3), 0)
            out = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 12)
        else:
            out = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 11)
        return Image.fromarray(out)

    if "_ocr_filter_original_build_po_document_template_v2" not in globals():
        _ocr_filter_original_build_po_document_template_v2 = build_po_document_template_v2

        def build_po_document_template_v2(pil_img, lang, template=None):  # type: ignore[no-redef]
            _set_ocr_filter_profile(template)
            return _ocr_filter_original_build_po_document_template_v2(pil_img, lang, template)
except Exception:
    pass
# === END OCR FILTER PROFILE PATCH ===

