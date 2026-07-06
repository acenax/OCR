"""OCR engine: PDF -> image -> table via ruled grid lines -> reconstructed PO.

These POs are faint dot-matrix scans with a ruled table. We:
  1. render the page,
  2. detect the table's vertical grid lines (robust column boundaries) and the
     header/body horizontal lines,
  3. drop each Tesseract word into the correct column band,
  4. parse the numeric columns with a fixed-2-decimal rule (baht amounts always
     print 2 decimals, but faint decimal points are lost by OCR, so digits/100
     recovers the true value).
The user verifies/edits everything in the UI afterwards.
"""
from __future__ import annotations

import re

import cv2
import numpy as np
import pandas as pd
import pytesseract
from pdf2image import convert_from_path
from PIL import Image

from .models import PODocument, POLine


HEADER_KEYWORDS = {
    "item": ["ITEM"],
    "code": ["CODE", "PRODUCT"],
    "desc": ["DESCRIPTION"],
    "qty": ["QTY", "QTY", "ATY"],           # Q'TY often OCRs as aTy/ATY
    "price": ["PRICE", "UNIT"],
    "amount": ["AMOUNT"],
}
COLUMN_ORDER = ["item", "code", "desc", "qty", "price", "amount"]
NUMERIC_COLS = {"qty", "price", "amount"}
DATE_RE = re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})")

# Default column centers as a fraction of page width (CMT layout) for fallback.
DEFAULT_ANCHORS = {
    "item": 0.10, "code": 0.21, "desc": 0.47,
    "qty": 0.71, "price": 0.82, "amount": 0.93,
}


def parse_fixed(text, scale: int) -> float | None:
    """Keep digits only and divide by `scale` — for fixed-decimal formats where
    OCR loses/garbles the separators. scale=100 -> 2 decimals, 1000 -> 3 decimals.
    '100.00'->100.0 (scale 100); '1,595.000'/'1.595.000'->1595.0 (scale 1000).
    """
    digits = re.sub(r"\D", "", str(text))
    if not digits:
        return None
    return int(digits) / scale


def parse_money(text) -> float | None:
    """Fixed 2-decimal parse (CMT dot-matrix)."""
    return parse_fixed(text, 100)


def parse_decimal(text) -> float | None:
    """Normal decimal parse for clean digital PDFs (e.g. NIPPON: '436.750', '1')."""
    m = re.search(r"-?[\d,]+(?:\.\d+)?", str(text).replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_num(text, number_mode: str) -> float | None:
    if number_mode == "fixed2":
        return parse_fixed(text, 100)
    if number_mode == "fixed3":
        return parse_fixed(text, 1000)
    return parse_decimal(text)


# === PHASE2 POPPLER PATCH ===
def _phase2_valid_poppler_bin(folder) -> str:
    """Return a valid Poppler bin folder or empty string."""
    try:
        from pathlib import Path
        p = Path(str(folder or "").strip().strip('"'))
        if not p:
            return ""
        if p.is_file() and p.name.lower() in {"pdftoppm.exe", "pdftocairo.exe", "pdftoppm", "pdftocairo"}:
            return str(p.parent)
        candidates = [
            p,
            p / "bin",
            p / "Library" / "bin",
            p / "poppler" / "bin",
            p / "poppler" / "Library" / "bin",
        ]
        for c in candidates:
            if (c / "pdftoppm.exe").exists() or (c / "pdftocairo.exe").exists() or (c / "pdftoppm").exists() or (c / "pdftocairo").exists():
                return str(c)
    except Exception:
        return ""
    return ""


def _phase2_resolve_poppler_path(pdf_path: str, configured: str = "") -> str:
    """Find Poppler from settings, project folders, PDF root folders, common Windows paths, or PATH."""
    from pathlib import Path
    import os
    import shutil

    raw_candidates = []
    if configured:
        raw_candidates.append(configured)
    env_poppler = os.environ.get("POPPLER_PATH") or os.environ.get("POPPLER_HOME")
    if env_poppler:
        raw_candidates.append(env_poppler)

    here = Path(__file__).resolve()              # .../app/ocr.py
    app_dir = here.parents[1]                    # .../TMC_OCR
    raw_candidates.extend([
        app_dir / "poppler" / "Library" / "bin",
        app_dir / "poppler" / "bin",
        app_dir / "poppler",
        app_dir.parent / "poppler" / "Library" / "bin",
        app_dir.parent / "poppler" / "bin",
        app_dir.parent / "poppler",
    ])

    try:
        p = Path(pdf_path).resolve()
        for parent in list(p.parents)[:6]:
            raw_candidates.extend([
                parent / "poppler" / "Library" / "bin",
                parent / "poppler" / "bin",
                parent / "poppler",
            ])
    except Exception:
        pass

    raw_candidates.extend([
        r"C:\poppler\Library\bin",
        r"C:\poppler\bin",
        r"C:\poppler",
        r"C:\Program Files\poppler\Library\bin",
        r"C:\Program Files\poppler\bin",
        r"C:\Program Files\poppler",
        r"C:\Program Files (x86)\poppler\Library\bin",
        r"C:\Program Files (x86)\poppler\bin",
    ])

    seen = set()
    for candidate in raw_candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        found = _phase2_valid_poppler_bin(candidate)
        if found:
            return found

    # If pdftoppm is already in PATH, pdf2image can work without poppler_path.
    if shutil.which("pdftoppm") or shutil.which("pdftocairo"):
        return ""
    return ""


def render_pdf(path: str, dpi: int, poppler_path: str) -> list[Image.Image]:
    kwargs = {"dpi": dpi}
    resolved_poppler = _phase2_resolve_poppler_path(path, poppler_path)
    if resolved_poppler:
        kwargs["poppler_path"] = resolved_poppler
    try:
        return convert_from_path(path, **kwargs)
    except Exception as e:
        msg = str(e)
        if "Unable to get page count" in msg or "poppler" in msg.lower() or "pdftoppm" in msg.lower() or "pdfinfo" in msg.lower():
            raise RuntimeError(
                "แปลง PDF ไม่สำเร็จ เพราะระบบไม่พบ Poppler/pdftoppm.exe\n\n"
                "วิธีแก้:\n"
                "1) เปิดแท็บ 4) ตั้งค่า แล้วตั้งค่า Poppler ไปที่โฟลเดอร์ bin ที่มี pdftoppm.exe\n"
                "   ตัวอย่าง: C:\\poppler\\Library\\bin\n"
                "2) หรือวางโฟลเดอร์ poppler ไว้ข้างโฟลเดอร์ TMC_OCR เช่น ..\\poppler\\Library\\bin\n"
                "3) ตรวจสอบได้ด้วยคำสั่ง: python check_poppler.py\n\n"
                f"ไฟล์: {path}\n"
                f"Poppler ที่ตั้งไว้: {poppler_path or '-'}\n"
                f"รายละเอียดเดิม: {msg}"
            ) from e
        raise

def preprocess(pil_img: Image.Image) -> np.ndarray:
    """For text OCR: grayscale + Otsu + light close to bridge dot-matrix gaps."""
    gray = cv2.cvtColor(np.array(pil_img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    kernel = np.ones((2, 2), np.uint8)
    return cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel, iterations=1)


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
    """Detect column boundaries and header/body y-range from the ruled table."""
    gray = cv2.cvtColor(np.array(pil_img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    h, w = inv.shape

    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, h // 40)))
    vert = cv2.morphologyEx(inv, cv2.MORPH_OPEN, vk)
    colsum = vert.sum(axis=0) / 255.0
    vxs = _group([x for x in range(w) if colsum[x] > h * 0.18], gap=6)

    # table y-range = where the vertical lines actually have ink
    rows_with_v = np.where(vert.sum(axis=1) > 0)[0]
    table_top = int(rows_with_v.min()) if rows_with_v.size else int(h * 0.30)
    table_bottom = int(rows_with_v.max()) if rows_with_v.size else int(h * 0.65)

    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, w // 40), 1))
    hor = cv2.morphologyEx(inv, cv2.MORPH_OPEN, hk)
    rowsum = hor.sum(axis=1) / 255.0
    hys = _group([y for y in range(h) if rowsum[y] > w * 0.30], gap=6)

    # header/data separator = first horizontal line a bit below the table top
    span = table_bottom - table_top
    data_top = table_top + int(span * 0.12)
    for y in hys:
        if table_top + span * 0.04 < y < table_top + span * 0.5:
            data_top = y
            break

    return {
        "width": w, "height": h,
        "vxs": vxs, "table_top": table_top, "table_bottom": table_bottom,
        "data_top": data_top, "hys": hys,
    }


def ocr_words(proc_img: np.ndarray, lang: str) -> pd.DataFrame:
    try:
        data = pytesseract.image_to_data(
            proc_img, lang=lang, config="--psm 6",
            output_type=pytesseract.Output.DATAFRAME,
        )
    except pytesseract.pytesseract.TesseractError:
        # requested language pack missing -> fall back to English
        data = pytesseract.image_to_data(
            proc_img, lang="eng", config="--psm 6",
            output_type=pytesseract.Output.DATAFRAME,
        )
    data = data.dropna(subset=["text"])
    data["text"] = data["text"].astype(str)
    data = data[data["text"].str.strip() != ""]
    data["conf"] = pd.to_numeric(data["conf"], errors="coerce").fillna(-1)
    data["cx"] = data["left"] + data["width"] / 2
    data["cy"] = data["top"] + data["height"] / 2
    return data.reset_index(drop=True)


def _boundaries_from_grid(grid: dict) -> list[float] | None:
    """Return 7 x-boundaries (6 columns) if grid detection looks right."""
    vxs = list(grid["vxs"])
    w = grid["width"]
    if not vxs:
        return None
    # ensure a right border exists
    if w - vxs[-1] > w * 0.05:
        vxs = vxs + [w]
    if len(vxs) == 7:
        return vxs
    if len(vxs) == 6:                       # right border missing -> add it
        return vxs + [w]
    return None


def _label_bands(boundaries: list[float]) -> list[tuple[str, float, float]]:
    """6 bands in known left-to-right order."""
    bands = []
    for i in range(len(boundaries) - 1):
        if i < len(COLUMN_ORDER):
            bands.append((COLUMN_ORDER[i], boundaries[i], boundaries[i + 1]))
    return bands


def _bands_from_anchors(anchors_frac: dict, w: int) -> list:
    present = sorted(((c, f * w) for c, f in anchors_frac.items()), key=lambda t: t[1])
    bands = []
    for i, (col, cx) in enumerate(present):
        lo = 0.0 if i == 0 else (present[i - 1][1] + cx) / 2
        hi = float(w) if i == len(present) - 1 else (present[i + 1][1] + cx) / 2
        bands.append((col, lo, hi))
    return bands


def _bands_from_headers(words, grid, data_top) -> tuple[list, dict]:
    """Fallback: build bands from header text anchors / defaults."""
    up = words.assign(U=words["text"].str.upper().str.replace(r"[^A-Z']", "", regex=True))
    anchors: dict[str, float] = {}
    for col, keys in HEADER_KEYWORDS.items():
        keyset = {re.sub(r"[^A-Z']", "", k.upper()) for k in keys}
        hit = up[(up["U"].isin(keyset)) & (up["top"] < data_top)]
        if not hit.empty:
            anchors[col] = float(hit["cx"].mean())
    w = grid["width"]
    if len(anchors) < 4:
        anchors = {c: f * w for c, f in DEFAULT_ANCHORS.items()}
    frac = {c: anchors[c] / w for c in anchors}
    return _bands_from_anchors(frac, w), frac


def _body_region(words, grid) -> tuple[float, float]:
    """Table body y-range from the header keyword row and the footer TOTAL row.

    Far more reliable than the ruled-line extent because letterhead/perforation
    noise can otherwise stretch the detected table.
    """
    up = words["text"].str.upper().str.replace(r"[^A-Z']", "", regex=True)
    h = grid["height"]
    header_keys = {"ITEM", "DESCRIPTION", "AMOUNT", "PRODUCT", "PRICE"}
    hmask = up.isin(header_keys) & (words["top"] < h * 0.55)
    if hmask.any():
        hdr = words[hmask]
        data_top = float((hdr["top"] + hdr["height"]).max()) + h * 0.006
    else:
        data_top = grid["data_top"]

    # footer = first TOTAL/GRAND/รวม indicator, or first ruled line, below data_top
    fmask = up.str.contains("TOTAL|GRAND", regex=True, na=False) & (words["top"] > data_top + h * 0.03)
    candidates = []
    if fmask.any():
        candidates.append(float(words[fmask]["top"].min()))
    for y in grid["hys"]:
        if y > data_top + h * 0.04:
            candidates.append(float(y))
            break
    table_bottom = min(candidates) - h * 0.004 if candidates else grid["table_bottom"]
    return data_top, table_bottom


def _assign_col(cx: float, bands) -> str | None:
    for col, lo, hi in bands:
        if lo <= cx < hi:
            return col
    return None


def _extract_rows(words, bands, data_top, table_bottom,
                  number_mode: str = "fixed2", name_below: bool = False) -> list[POLine]:
    body = words[(words["cy"] > data_top) & (words["cy"] < table_bottom)].copy()
    if body.empty:
        return []
    body["line_id"] = (body["block_num"] * 1_000_000 + body["par_num"] * 10_000
                       + body["line_num"] * 100 + (body["top"] // 30))
    qty_band = next((b for b in bands if b[0] == "qty"), None)
    left_cut = qty_band[1] if qty_band else float("inf")

    # build one record per text line, in vertical order
    recs = []
    for _, grp in body.groupby("line_id"):
        cells: dict[str, list] = {c: [] for c, _, _ in bands}
        left_words = []
        for _, wd in grp.sort_values("left").iterrows():
            col = _assign_col(wd["cx"], bands)
            if col is not None:
                cells[col].append(wd["text"])
            if wd["cx"] < left_cut:
                left_words.append(wd["text"])
        qty = _parse_num(" ".join(cells.get("qty", [])), number_mode)
        price = _parse_num(" ".join(cells.get("price", [])), number_mode)
        amount = _parse_num(" ".join(cells.get("amount", [])), number_mode)
        recs.append({
            "top": float(grp["top"].min()),
            "item": " ".join(cells.get("item", [])).strip(),
            "code": " ".join(cells.get("code", [])).strip(),
            "desc": " ".join(cells.get("desc", [])).strip(),
            "left": " ".join(left_words).strip(),
            "qty": qty, "price": price, "amount": amount,
        })
    recs.sort(key=lambda r: r["top"])

    lines: list[POLine] = []
    if name_below:
        # each item = a numeric line (has an AMOUNT), then its real name on the
        # following line(s). qty is derived from amount/price because the qty
        # column often OCRs poorly on these layouts.
        i = 0
        while i < len(recs):
            r = recs[i]
            if r["amount"] is not None:
                parts, j = [], i + 1
                while j < len(recs) and recs[j]["amount"] is None:
                    nm = (recs[j]["item"] + " " + recs[j]["desc"]).strip()
                    if nm:
                        parts.append(nm)
                    j += 1
                name = " ".join(parts).strip() or r["desc"] or r["left"]
                if len(name.strip()) < 3:
                    # a numeric line with no product name after it = a footer/total row
                    i = j
                    continue
                price, amount = r["price"], r["amount"]
                # qty column OCRs poorly here -> derive from amount/price
                if price and price > 0:
                    ratio = amount / price
                    if abs(ratio - round(ratio)) < 0.05 and 0 < round(ratio) < 100000:
                        qty = float(round(ratio))
                    else:                           # messy unit price -> treat as single unit
                        qty, price = 1.0, amount
                else:
                    qty, price = 1.0, amount
                lines.append(POLine(
                    item_no=r["item"], product_code_raw=(r["desc"] or r["left"]),
                    description_raw=name,
                    qty=qty or 0.0, price=price or 0.0, amount=amount or 0.0))
                i = j
            else:
                i += 1
    else:
        for r in recs:
            if not r["desc"] and not r["code"]:
                continue
            if r["qty"] is None and r["price"] is None and r["amount"] is None:
                continue
            lines.append(POLine(
                item_no=r["item"], product_code_raw=r["code"], description_raw=r["desc"],
                qty=r["qty"] or 0.0, price=r["price"] or 0.0, amount=r["amount"] or 0.0))
    return lines


def _extract_dates(words) -> str:
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
    if y > 2400:          # Buddhist year -> Gregorian
        y -= 543
    try:
        return f"{y:04d}-{int(mo):02d}-{int(d):02d}"
    except ValueError:
        return ""


def _footer_totals(words, bands, table_bottom) -> tuple:
    amount_band = next((b for b in bands if b[0] == "amount"), None)
    if amount_band is None:
        return None, None, None
    _, lo, hi = amount_band
    foot = words[(words["cy"] > table_bottom)
                 & (words["cx"] >= lo - (hi - lo) * 0.4) & (words["cx"] <= hi)].copy()
    if foot.empty:
        return None, None, None
    nums = []
    for _, grp in foot.groupby(foot["top"] // 30):
        v = parse_money(" ".join(grp.sort_values("left")["text"]))
        if v and v > 0:
            nums.append((float(grp["top"].min()), v))
    nums.sort()
    vals = [v for _, v in nums]
    if len(vals) >= 3:
        return vals[0], vals[1], vals[2]
    if len(vals) == 2:
        return vals[0], None, vals[1]
    return None, None, None


def build_po_document(pil_img: Image.Image, lang: str, template: dict | None = None) -> PODocument:
    doc = PODocument()
    profile = template or {}
    mode = profile.get("mode")                 # "grid" (default) | "anchors"
    name_below = bool(profile.get("name_below", False))

    grid = detect_grid(pil_img)
    proc = preprocess(pil_img)
    words = ocr_words(proc, lang)
    if words.empty:
        doc.warnings.append("OCR ไม่พบข้อความในหน้าเอกสาร")
        return doc

    cols = profile.get("columns")   # explicit boxes drawn by the user (manual teaching)
    if cols:
        W = grid["width"]
        bands = sorted([(name, lo * W, hi * W) for name, (lo, hi) in cols.items()],
                       key=lambda b: b[1])
        anchors_frac = {n: (lo + hi) / 2 / W for n, lo, hi in bands}
        used_template = True
        h = float(grid["height"])
        data_top = profile.get("data_top_frac", 0.0) * h
        table_bottom = profile.get("bottom_frac", 1.0) * h
    elif mode == "anchors" and profile.get("anchors"):
        # customer profile with fixed column positions (no ruled table / no header)
        anchors_frac = profile["anchors"]
        bands = _bands_from_anchors(anchors_frac, grid["width"])
        used_template = True
        # skip the repeated letterhead/address (top) and page footer (bottom)
        h = float(grid["height"])
        data_top = profile.get("data_top_frac", 0.0) * h
        table_bottom = profile.get("bottom_frac", 1.0) * h
    else:
        data_top, table_bottom = _body_region(words, grid)
        boundaries = _boundaries_from_grid(grid)
        if boundaries:
            bands = _label_bands(boundaries)
            anchors_frac = {c: (lo + hi) / 2 / grid["width"] for c, lo, hi in bands}
            used_template = False
        elif profile.get("anchors"):
            anchors_frac = profile["anchors"]
            bands = _bands_from_anchors(anchors_frac, grid["width"])
            used_template = True
            doc.warnings.append("ใช้ตำแหน่งคอลัมน์ที่เรียนรู้ไว้ของลูกค้านี้ (แนะนำให้ตรวจทาน)")
        else:
            bands, anchors_frac = _bands_from_headers(words, grid, data_top)
            used_template = True
            doc.warnings.append("ตรวจไม่พบเส้นตาราง/ยังไม่มีตำแหน่งที่เรียนรู้ ใช้ค่าเริ่มต้น (แนะนำให้ตรวจทาน)")

    number_mode = profile.get("number_mode") or ("decimal" if mode == "anchors" else "fixed2")
    doc.lines = _extract_rows(words, bands, data_top, table_bottom, number_mode, name_below)

    doc.po_date_raw = _extract_dates(words)
    doc.po_date = _iso_date(doc.po_date_raw)

    # totals: qty*price is the most reliable (those are the exported fields)
    sum_lines = round(sum(l.qty * l.price for l in doc.lines), 2)
    printed_total, _, printed_grand = _footer_totals(words, bands, table_bottom)
    # trust the printed total only when it is within 20% of the line sum
    if printed_total and sum_lines and abs(printed_total - sum_lines) <= sum_lines * 0.2:
        doc.total = printed_total
    else:
        doc.total = sum_lines
    doc.vat = round(doc.total * 0.07, 2)
    doc.grand_total = round(doc.total + doc.vat, 2)

    if not doc.lines:
        doc.warnings.append("ไม่พบรายการสินค้า — โปรดตรวจทาน/เรียนรู้ตำแหน่งใหม่")
    if printed_total and sum_lines and abs(printed_total - sum_lines) > max(1.0, sum_lines * 0.02):
        doc.warnings.append(
            f"ยอดพิมพ์ ({printed_total:,.2f}) ไม่ตรงผลรวม qty×price ({sum_lines:,.2f}) — โปรดตรวจ")

    doc._anchors_frac = anchors_frac                 # type: ignore[attr-defined]
    doc._header_bottom = data_top / grid["height"]   # type: ignore[attr-defined]
    doc._used_template = used_template               # type: ignore[attr-defined]
    return doc
