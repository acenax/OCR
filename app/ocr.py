"""OCR engine for CMT/TMC PO documents.

Phase 14 goals:
- Trust printed footer totals when they are readable. Do not overwrite header totals
  with a bad line-sum when OCR line items are imperfect.
- Use manually taught full column boxes as a strict table body area, so footer/header
  text is not treated as product lines.
- Parse scanned money/quantity columns with a fixed 2-decimal strategy, then repair
  price/amount using qty x price = amount.
- Keep the old public API: render_pdf(...), build_po_document(...).
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable

import cv2
import numpy as np
import pandas as pd
import pytesseract
from pdf2image import convert_from_path
from PIL import Image

from .models import PODocument, POLine

HEADER_KEYWORDS = {
    "item": ["ITEM", "ลำดับ"],
    "code": ["CODE", "PRODUCT", "รหัสสินค้า"],
    "desc": ["DESCRIPTION", "รายการ"],
    "qty": ["QTY", "Q'TY", "จำนวน"],
    "price": ["PRICE", "UNIT", "หน่วยละ"],
    "amount": ["AMOUNT", "จำนวนเงิน"],
}

COLUMN_ORDER = ["item", "code", "desc", "qty", "price", "amount"]
NUMERIC_COLS = {"qty", "price", "amount"}
DATE_RE = re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})")

DEFAULT_ANCHORS = {
    "item": 0.065,
    "code": 0.155,
    "desc": 0.405,
    "qty": 0.675,
    "price": 0.795,
    "amount": 0.925,
}

_DIGIT_TRANSLATION = str.maketrans(
    "๐๑๒๓๔๕๖๗๘๙٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹０１２３４５６７８９",
    "0123456789012345678901234567890123456789",
)


def normalize_digits(text: str | float | int | None) -> str:
    if text is None:
        return ""
    return str(text).translate(_DIGIT_TRANSLATION)


def _digits(text: str | float | int | None) -> str:
    return re.sub(r"\D", "", normalize_digits(text))


def parse_fixed(text, scale: int = 100) -> float | None:
    d = _digits(text)
    if not d:
        return None
    try:
        return int(d) / float(scale)
    except Exception:
        return None


def parse_money(text) -> float | None:
    """Parse scanned money/qty cells. For these POs, dots/commas are often lost,
    so digits/100 is the most stable representation.

    Now delegates to the shared parser in ocr_numbers.py so this stays in
    sync with ocr_tuning.py / ocr_stable.py / ocr_template_v2.py, which
    previously each guessed the decimal position differently.
    """
    from .ocr_numbers import parse_scanned_number
    return parse_scanned_number(text, "amount")


def parse_decimal(text) -> float | None:
    s = normalize_digits(text).replace(" ", "")
    m = re.search(r"-?[0-9,]+(?:\.[0-9]+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except Exception:
        return None


def _parse_num(text, number_mode: str = "fixed2", column: str = "") -> float | None:
    """Parse table numbers.

    For scanned PO tables, force fixed-2 for qty/price/amount even if the profile
    was accidentally saved as decimal. This prevents values such as 6.25 becoming
    0.0062 or 3,487.50 becoming 3.49 after OCR noise.
    """
    if column in NUMERIC_COLS:
        from .ocr_numbers import parse_scanned_number
        v = parse_scanned_number(text, column)
        if v is not None:
            return v
    if number_mode == "fixed3":
        return parse_fixed(text, 1000)
    if number_mode == "decimal":
        v = parse_decimal(text)
        if v is not None:
            return v
    return parse_fixed(text, 100)


def render_pdf(path: str, dpi: int, poppler_path: str | None = None) -> list[Image.Image]:
    kwargs = {"dpi": int(dpi or 300)}
    if poppler_path:
        kwargs["poppler_path"] = poppler_path
    return convert_from_path(path, **kwargs)


def preprocess(pil_img: Image.Image) -> np.ndarray:
    """High-contrast preprocessing for faint/colour-shifted scans."""
    rgb = np.array(pil_img.convert("RGB"))
    from .ocr_text import remove_table_gridlines
    rgb = remove_table_gridlines(rgb)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    # remove mild colour cast / uneven background
    gray = cv2.bilateralFilter(gray, 5, 45, 45)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    th = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11
    )
    # close dot-matrix gaps, but keep table text readable
    kernel = np.ones((1, 1), np.uint8)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel, iterations=1)
    return th


def _group(values: list[int], gap: int) -> list[int]:
    if not values:
        return []
    values = sorted(values)
    out, cur = [], [values[0]]
    for v in values[1:]:
        if v - cur[-1] <= gap:
            cur.append(v)
        else:
            out.append(int(np.mean(cur)))
            cur = [v]
    out.append(int(np.mean(cur)))
    return out


def detect_grid(pil_img: Image.Image) -> dict:
    gray = cv2.cvtColor(np.array(pil_img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    gray = cv2.bilateralFilter(gray, 5, 35, 35)
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    h, w = inv.shape

    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 35)))
    vert = cv2.morphologyEx(inv, cv2.MORPH_OPEN, vk)
    colsum = vert.sum(axis=0) / 255.0
    vxs = _group([x for x in range(w) if colsum[x] > h * 0.12], gap=max(4, w // 300))

    rows_with_v = np.where(vert.sum(axis=1) > 0)[0]
    table_top = int(rows_with_v.min()) if rows_with_v.size else int(h * 0.30)
    table_bottom = int(rows_with_v.max()) if rows_with_v.size else int(h * 0.72)

    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, w // 25), 1))
    hor = cv2.morphologyEx(inv, cv2.MORPH_OPEN, hk)
    rowsum = hor.sum(axis=1) / 255.0
    hys = _group([y for y in range(h) if rowsum[y] > w * 0.25], gap=max(4, h // 400))

    span = max(1, table_bottom - table_top)
    data_top = table_top + int(span * 0.12)
    for y in hys:
        if table_top + span * 0.04 < y < table_top + span * 0.45:
            data_top = y
            break
    return {
        "width": w,
        "height": h,
        "vxs": vxs,
        "table_top": table_top,
        "table_bottom": table_bottom,
        "data_top": data_top,
        "hys": hys,
    }


def ocr_words(proc_img: np.ndarray, lang: str) -> pd.DataFrame:
    configs = ["--oem 3 --psm 6", "--oem 3 --psm 11"]
    last_err = None
    for config in configs:
        try:
            data = pytesseract.image_to_data(
                proc_img,
                lang=lang or "tha+eng",
                config=config,
                output_type=pytesseract.Output.DATAFRAME,
            )
            break
        except Exception as e:
            last_err = e
            try:
                data = pytesseract.image_to_data(
                    proc_img,
                    lang="eng",
                    config=config,
                    output_type=pytesseract.Output.DATAFRAME,
                )
                break
            except Exception as e2:
                last_err = e2
    else:
        raise last_err or RuntimeError("Tesseract OCR failed")

    data = data.dropna(subset=["text"])
    data["text"] = data["text"].astype(str).map(normalize_digits)
    data = data[data["text"].str.strip() != ""]
    data["conf"] = pd.to_numeric(data.get("conf", -1), errors="coerce").fillna(-1)
    data["cx"] = data["left"] + data["width"] / 2
    data["cy"] = data["top"] + data["height"] / 2
    return data.reset_index(drop=True)


def _boundaries_from_grid(grid: dict) -> list[float] | None:
    vxs = list(grid.get("vxs") or [])
    w = float(grid["width"])
    if len(vxs) < 5:
        return None
    # keep only major boundaries and make sure left/right edges exist
    vxs = [x for x in vxs if 0 <= x <= w]
    if not vxs:
        return None
    if vxs[0] > w * 0.08:
        vxs.insert(0, 0)
    if w - vxs[-1] > w * 0.04:
        vxs.append(w)
    if len(vxs) >= 7:
        # choose the best 7 boundaries spanning the printed table
        return vxs[:7]
    if len(vxs) == 6:
        return vxs + [w]
    return None


def _label_bands(boundaries: list[float]) -> list[tuple[str, float, float]]:
    return [(COLUMN_ORDER[i], boundaries[i], boundaries[i + 1]) for i in range(min(6, len(boundaries)-1))]


def _bands_from_anchors(anchors_frac: dict, w: int) -> list[tuple[str, float, float]]:
    present = sorted(((c, float(f) * w) for c, f in anchors_frac.items() if c in COLUMN_ORDER), key=lambda t: t[1])
    bands = []
    for i, (col, cx) in enumerate(present):
        lo = 0.0 if i == 0 else (present[i - 1][1] + cx) / 2
        hi = float(w) if i == len(present) - 1 else (present[i + 1][1] + cx) / 2
        bands.append((col, lo, hi))
    return bands


def _bands_from_profile(profile: dict, grid: dict):
    w, h = float(grid["width"]), float(grid["height"])
    boxes = profile.get("boxes") or profile.get("field_boxes") or profile.get("rects")
    if isinstance(boxes, dict) and boxes:
        bands = []
        tops, bottoms = [], []
        for key, val in boxes.items():
            if key not in COLUMN_ORDER:
                continue
            if isinstance(val, dict):
                l = float(val.get("left", val.get("x1", 0)))
                t = float(val.get("top", val.get("y1", 0)))
                r = float(val.get("right", val.get("x2", 0)))
                b = float(val.get("bottom", val.get("y2", 0)))
            else:
                arr = list(val)
                if len(arr) >= 4:
                    l, t, r, b = map(float, arr[:4])
                elif len(arr) >= 2:
                    # old x-only box, no y information
                    l, r = map(float, arr[:2])
                    t = float(profile.get("data_top_frac", 0.0))
                    b = float(profile.get("bottom_frac", 1.0))
                else:
                    continue
            # values are stored as fractions
            if max(l, t, r, b) <= 1.5:
                l, r = l * w, r * w
                t, b = t * h, b * h
            if r < l:
                l, r = r, l
            if b < t:
                t, b = b, t
            bands.append((key, max(0, l), min(w, r)))
            tops.append(max(0, t)); bottoms.append(min(h, b))
        if bands:
            bands.sort(key=lambda x: x[1])
            return bands, max(0, min(tops) - h * 0.003), min(h, max(bottoms) + h * 0.003), True

    cols = profile.get("columns")
    if isinstance(cols, dict) and cols:
        bands = []
        for key, val in cols.items():
            if key not in COLUMN_ORDER:
                continue
            try:
                lo, hi = float(val[0]), float(val[1])
            except Exception:
                continue
            bands.append((key, lo * w if lo <= 1.5 else lo, hi * w if hi <= 1.5 else hi))
        if bands:
            bands.sort(key=lambda x: x[1])
            return (
                bands,
                float(profile.get("data_top_frac", 0.0)) * h,
                float(profile.get("bottom_frac", 1.0)) * h,
                True,
            )
    return None, None, None, False


def _bands_from_headers(words: pd.DataFrame, grid: dict, data_top: float):
    up = words.assign(U=words["text"].str.upper().str.replace(r"[^A-Z'ก-๙]", "", regex=True))
    anchors: dict[str, float] = {}
    for col, keys in HEADER_KEYWORDS.items():
        keyset = {re.sub(r"[^A-Z'ก-๙]", "", k.upper()) for k in keys}
        hit = up[(up["U"].isin(keyset)) & (up["top"] < data_top + grid["height"] * 0.05)]
        if not hit.empty:
            anchors[col] = float(hit["cx"].mean())
    w = grid["width"]
    if len(anchors) < 4:
        anchors = {c: f * w for c, f in DEFAULT_ANCHORS.items()}
    frac = {c: anchors[c] / w for c in anchors}
    return _bands_from_anchors(frac, w), frac


def _body_region(words: pd.DataFrame, grid: dict) -> tuple[float, float]:
    up = words["text"].str.upper().str.replace(r"[^A-Z'ก-๙]", "", regex=True)
    h = grid["height"]
    header_keys = {"ITEM", "DESCRIPTION", "AMOUNT", "PRODUCT", "PRICE", "QTY", "จำนวน", "รายการ"}
    hmask = up.isin(header_keys) & (words["top"] < h * 0.58)
    if hmask.any():
        hdr = words[hmask]
        data_top = float((hdr["top"] + hdr["height"]).max()) + h * 0.005
    else:
        data_top = float(grid["data_top"])

    fmask = up.str.contains("TOTAL|GRAND|รวม|ภาษี", regex=True, na=False) & (words["top"] > data_top + h * 0.05)
    candidates = []
    if fmask.any():
        candidates.append(float(words[fmask]["top"].min()))
    for y in grid.get("hys") or []:
        if y > data_top + h * 0.04:
            candidates.append(float(y))
            break
    bottom = min(candidates) - h * 0.004 if candidates else float(grid["table_bottom"])
    return data_top, bottom


def _assign_col(cx: float, bands) -> str | None:
    for col, lo, hi in bands:
        if lo <= cx < hi:
            return col
    return None


def _cluster_rows(body: pd.DataFrame) -> list[pd.DataFrame]:
    if body.empty:
        return []
    rows = []
    cur = []
    cur_cy = None
    heights = body["height"].astype(float)
    tol = max(10.0, min(28.0, float(heights.median() if not heights.empty else 16) * 0.75))
    for _, wd in body.sort_values(["cy", "left"]).iterrows():
        cy = float(wd["cy"])
        if cur_cy is None or abs(cy - cur_cy) <= tol:
            cur.append(wd)
            cur_cy = cy if cur_cy is None else (cur_cy * (len(cur)-1) + cy) / len(cur)
        else:
            rows.append(pd.DataFrame(cur))
            cur = [wd]
            cur_cy = cy
    if cur:
        rows.append(pd.DataFrame(cur))
    return rows


def _line_from_cells(cells: dict[str, list[str]], number_mode: str) -> POLine | None:
    from .ocr_text import clean_ocr_text
    item = " ".join(cells.get("item", [])).strip()
    code = clean_ocr_text(" ".join(cells.get("code", [])).strip())
    desc = clean_ocr_text(" ".join(cells.get("desc", [])).strip())
    qty = _parse_num(" ".join(cells.get("qty", [])), number_mode, "qty")
    price = _parse_num(" ".join(cells.get("price", [])), number_mode, "price")
    amount = _parse_num(" ".join(cells.get("amount", [])), number_mode, "amount")

    joined = " ".join([item, code, desc]).upper()
    if re.search(r"TOTAL|GRAND|VAT|ภาษี|รวม", joined):
        return None
    if not code and not desc:
        return None
    if qty is None and price is None and amount is None:
        return None

    line = POLine(
        item_no=item,
        product_code_raw=code,
        description_raw=desc,
        qty=qty or 0.0,
        price=price or 0.0,
        amount=amount or 0.0,
    )
    return _repair_line_numbers(line)


def _repair_line_numbers(line: POLine) -> POLine:
    q, p, a = float(line.qty or 0), float(line.price or 0), float(line.amount or 0)

    # If OCR lost decimals heavily, choose the most plausible relationship.
    if q > 0 and p > 0 and a > 0:
        expected = q * p
        diff = abs(expected - a)
        tol = max(1.0, max(expected, a) * 0.03)
        if diff > tol:
            if a > expected * 2.5:
                # Amount is likely right; unit price lost a zero/decimal.
                p = a / q
            elif expected > a * 2.5:
                # Price is likely right; amount OCR lost digits.
                a = expected
            else:
                # Small mismatch: prefer printed amount, derive price.
                p = a / q
    elif q > 0 and a > 0 and p <= 0:
        p = a / q
    elif q > 0 and p > 0 and a <= 0:
        a = q * p
    elif a > 0 and p > 0 and q <= 0:
        ratio = a / p
        if 0 < ratio < 100000:
            q = round(ratio, 4)

    # Clean floating noise.
    line.qty = round(q, 4)
    line.price = round(p, 4)
    line.amount = round(a, 2)
    return line


def _extract_rows(words: pd.DataFrame, bands, data_top, table_bottom, number_mode: str = "fixed2", name_below: bool = False) -> list[POLine]:
    body = words[(words["cy"] > data_top) & (words["cy"] < table_bottom)].copy()
    if body.empty:
        return []
    # Drop obvious table headers that sometimes leak into body.
    body = body[~body["text"].str.upper().str.contains(r"ITEM|PRODUCT|DESCRIPTION|QTY|PRICE|AMOUNT|TOTAL|GRAND", regex=True, na=False)]

    recs = []
    for row_df in _cluster_rows(body):
        cells: dict[str, list[str]] = {c: [] for c, _, _ in bands}
        for _, wd in row_df.sort_values("left").iterrows():
            col = _assign_col(float(wd["cx"]), bands)
            if col:
                cells.setdefault(col, []).append(str(wd["text"]))
        line = _line_from_cells(cells, number_mode)
        if line:
            recs.append(line)

    # Merge obvious continuation lines into previous item.
    lines: list[POLine] = []
    for ln in recs:
        if lines and not ln.product_code_raw and ln.description_raw and ln.qty <= 0 and ln.price <= 0:
            lines[-1].description_raw = (lines[-1].description_raw + " " + ln.description_raw).strip()
            continue
        # avoid duplicate continuation rows that repeat only amount/no code
        if lines and not ln.product_code_raw and ln.amount and ln.qty <= 0 and ln.price <= 0:
            continue
        lines.append(ln)

    # Sort / renumber if item OCR is missing.
    for i, ln in enumerate(lines, 1):
        if not str(ln.item_no).strip() or not re.search(r"\d", str(ln.item_no)):
            ln.item_no = str(i)
    return lines


def _extract_dates(words: pd.DataFrame) -> str:
    text = " ".join(words["text"].tolist())
    best = ""
    for m in DATE_RE.finditer(text):
        d, mo, y = m.groups()
        if len(y) == 4:
            return f"{d}/{mo}/{y}"
        best = best or f"{d}/{mo}/{y}"
    return best


def _iso_date(raw: str) -> str:
    m = DATE_RE.search(raw or "")
    if not m:
        return ""
    d, mo, y = m.groups()
    y = int(y)
    if y < 100:
        y += 2000
    if y > 2400:
        y -= 543
    try:
        return f"{y:04d}-{int(mo):02d}-{int(d):02d}"
    except Exception:
        return ""


def _numbers_by_visual_line(words: pd.DataFrame) -> list[tuple[float, float]]:
    out = []
    for grp in _cluster_rows(words):
        txt = " ".join(grp.sort_values("left")["text"].astype(str))
        # Prefer full numeric groups if tesseract kept commas/dots.
        candidates = re.findall(r"[0-9][0-9,\.]*", normalize_digits(txt))
        vals = []
        for c in candidates:
            v = parse_money(c)
            if v is not None and v > 0:
                vals.append(v)
        if vals:
            # right-most/last printed number on that line is usually the total value
            out.append((float(grp["top"].min()), vals[-1]))
    out.sort(key=lambda x: x[0])
    return out


def _footer_totals(words: pd.DataFrame, bands, table_bottom) -> tuple[float | None, float | None, float | None]:
    h = float(words["cy"].max() if not words.empty else 0)
    w = float(words["cx"].max() if not words.empty else 0)
    amount_band = next((b for b in bands if b[0] == "amount"), None)
    if amount_band:
        _, lo, hi = amount_band
        foot = words[(words["cy"] > table_bottom) & (words["cx"] >= lo - (hi - lo) * 1.0)].copy()
    else:
        foot = words[(words["cy"] > table_bottom) & (words["cx"] > w * 0.45)].copy()
    if foot.empty:
        foot = words[(words["cy"] > h * 0.70) & (words["cx"] > w * 0.45)].copy()
    if foot.empty:
        return None, None, None

    vals = [v for _, v in _numbers_by_visual_line(foot)]
    # Find a triple: total, VAT, grand total.
    for i in range(max(0, len(vals)-2)):
        t, vat, g = vals[i], vals[i+1], vals[i+2]
        if t > 0 and vat > 0 and g > 0:
            if abs(vat - t * 0.07) <= max(2.0, t * 0.015) and abs(g - (t + vat)) <= max(2.0, g * 0.01):
                return round(t, 2), round(vat, 2), round(g, 2)
    # Fallback: use last 3 plausible values in footer.
    plausible = [v for v in vals if 1 <= v <= 1_000_000_000]
    if len(plausible) >= 3:
        t, vat, g = plausible[-3:]
        return round(t, 2), round(vat, 2), round(g, 2)
    return None, None, None


def _choose_totals(doc: PODocument, printed_total, printed_vat, printed_grand):
    line_total = round(sum((l.amount if l.amount > 0 else l.qty * l.price) for l in doc.lines), 2)
    if printed_total and printed_grand:
        # Critical fix: footer total is the source of truth if readable.
        doc.total = round(printed_total, 2)
        doc.vat = round(printed_vat if printed_vat is not None else printed_total * 0.07, 2)
        doc.grand_total = round(printed_grand, 2)
        if line_total and abs(line_total - doc.total) > max(1.0, doc.total * 0.02):
            doc.warnings.append(
                f"ยอดรายการ ({line_total:,.2f}) ยังไม่ตรงยอดท้ายบิล ({doc.total:,.2f}) — กรุณาตรวจรายการ/สอนกรอบใหม่"
            )
        return
    doc.total = line_total
    doc.vat = round(doc.total * 0.07, 2)
    doc.grand_total = round(doc.total + doc.vat, 2)


def build_po_document(pil_img: Image.Image, lang: str, template: dict | None = None) -> PODocument:
    doc = PODocument()
    profile = template or {}
    grid = detect_grid(pil_img)
    proc = preprocess(pil_img)
    words = ocr_words(proc, lang)
    if words.empty:
        doc.warnings.append("OCR ไม่พบข้อความในหน้าเอกสาร")
        return doc

    # 1) Use manual full boxes first.
    bands, data_top, table_bottom, used_template = _bands_from_profile(profile, grid)
    anchors_frac = {}
    if not bands:
        data_top, table_bottom = _body_region(words, grid)
        boundaries = _boundaries_from_grid(grid)
        if boundaries:
            bands = _label_bands(boundaries)
            anchors_frac = {c: (lo + hi) / 2 / grid["width"] for c, lo, hi in bands}
            used_template = False
        elif profile.get("anchors"):
            anchors_frac = profile["anchors"]
            bands = _bands_from_anchors(anchors_frac, grid["width"])
            h = float(grid["height"])
            data_top = float(profile.get("data_top_frac", 0.0)) * h
            table_bottom = float(profile.get("bottom_frac", 1.0)) * h
            used_template = True
            doc.warnings.append("ใช้ตำแหน่งที่เรียนรู้ไว้ของลูกค้านี้")
        else:
            bands, anchors_frac = _bands_from_headers(words, grid, data_top)
            used_template = True
            doc.warnings.append("ตรวจตำแหน่งอัตโนมัติ — แนะนำให้สอนตำแหน่งถ้าข้อมูลไม่ตรง")

    number_mode = profile.get("number_mode") or "fixed2"
    # Force fixed2 on CMT-style scanned POs unless explicitly requested otherwise.
    if number_mode not in {"fixed2", "fixed3", "decimal"}:
        number_mode = "fixed2"

    doc.lines = _extract_rows(words, bands, data_top, table_bottom, number_mode, bool(profile.get("name_below", False)))
    doc.po_date_raw = _extract_dates(words)
    doc.po_date = _iso_date(doc.po_date_raw)

    printed_total, printed_vat, printed_grand = _footer_totals(words, bands, table_bottom)
    _choose_totals(doc, printed_total, printed_vat, printed_grand)

    if not doc.lines:
        doc.warnings.append("ไม่พบรายการสินค้า — โปรดสอนตำแหน่งใหม่ โดยลากเฉพาะพื้นที่รายการสินค้า ไม่รวมยอดรวมท้ายบิล")

    doc._anchors_frac = anchors_frac  # type: ignore[attr-defined]
    doc._header_bottom = data_top / grid["height"]  # type: ignore[attr-defined]
    doc._used_template = used_template  # type: ignore[attr-defined]
    doc._printed_totals = (printed_total, printed_vat, printed_grand)  # type: ignore[attr-defined]
    return doc

# === OCR TUNING NO PHASE17 WRAPPER ===
# Added by OCR tuning patch. This intentionally skips manual TOTAL/VAT/GRAND TOTAL boxes.
try:
    _ocr_tuning_original_build_po_document = build_po_document

    def build_po_document(pil_img, lang: str, template: dict | None = None):  # type: ignore[override]
        base_doc = _ocr_tuning_original_build_po_document(pil_img, lang, template)
        try:
            from . import ocr_tuning
            if template and isinstance(template, dict) and (template.get("columns") or template.get("boxes")):
                tuned = ocr_tuning.build_template_document(pil_img, lang, template, base_doc=base_doc)
                if tuned is not None and getattr(tuned, "lines", None):
                    return tuned
            return ocr_tuning.tune_document(base_doc, pil_img, lang, template)
        except Exception as exc:
            try:
                base_doc.warnings.append(f"OCR tuning ข้ามชั่วคราว: {exc}")
            except Exception:
                pass
            return base_doc
except Exception:
    pass
# === END OCR TUNING NO PHASE17 WRAPPER ===


# === STABLE TEMPLATE ENGINE OVERRIDE ===
# This override runs last. It uses the manually taught full boxes first.
# If the template is incomplete or the stable engine fails, it falls back to the previous engine.
try:
    _previous_build_po_document = build_po_document
    from . import ocr_stable as _stable_template_engine

    def build_po_document(pil_img, lang, template=None):
        try:
            doc = _stable_template_engine.build_po_document(pil_img, lang, template)
            if getattr(doc, "lines", None):
                return doc
            fallback = _previous_build_po_document(pil_img, lang, template)
            try:
                fallback.warnings = list(getattr(doc, "warnings", [])) + list(getattr(fallback, "warnings", []))
            except Exception:
                pass
            return fallback
        except Exception as exc:
            fallback = _previous_build_po_document(pil_img, lang, template)
            try:
                fallback.warnings.append(f"stable_template_engine fallback: {exc}")
            except Exception:
                pass
            return fallback
except Exception:
    pass


# === TEMPLATE OCR V2 ROW ENGINE PATCH ===
# This wrapper makes the taught layout boxes the primary OCR engine.
try:
    _template_v2_original_build_po_document = build_po_document
    from .ocr_template_v2 import build_po_document_template_v2 as _template_v2_build

    def build_po_document(pil_img, lang, template=None):  # type: ignore[no-redef]
        if template:
            try:
                new_doc = _template_v2_build(pil_img, lang, template)
                if getattr(new_doc, "lines", None):
                    try:
                        old_doc = _template_v2_original_build_po_document(pil_img, lang, template)
                        if old_doc:
                            if not new_doc.po_no:
                                new_doc.po_no = getattr(old_doc, "po_no", "")
                            if not new_doc.po_date:
                                new_doc.po_date = getattr(old_doc, "po_date", "")
                                new_doc.po_date_raw = getattr(old_doc, "po_date_raw", "")
                    except Exception:
                        pass
                    return new_doc
            except Exception as _e:
                try:
                    fallback_doc = _template_v2_original_build_po_document(pil_img, lang, template)
                    fallback_doc.warnings.append(f"Template OCR v2 ใช้ไม่ได้: {_e}")
                    return fallback_doc
                except Exception:
                    raise
        return _template_v2_original_build_po_document(pil_img, lang, template)
except Exception:
    pass
# === END TEMPLATE OCR V2 ROW ENGINE PATCH ===

