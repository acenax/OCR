
"""Main OCR public API with PaddleOCR as primary engine."""

from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import pytesseract
from pdf2image import convert_from_path
from PIL import Image

from .models import PODocument, POLine
from .ocr_image_filters import enhance_for_ocr
from .ocr_numbers import normalize_digits, parse_scanned_number
from .ocr_template_v2 import build_po_document_template_v2, repair_line_numbers
from .ocr_text import clean_part_description, clean_ocr_text
from .paddle_ocr_engine import extract_words, words_to_text

DATE_RE = re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})")


def _valid_poppler_path(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    has_pdfinfo = (p / "pdfinfo.exe").exists() or (p / "pdfinfo").exists()
    has_renderer = any((p / name).exists() for name in ("pdftoppm.exe", "pdftocairo.exe", "pdftoppm", "pdftocairo"))
    return str(p) if has_pdfinfo and has_renderer else None


def _auto_poppler_candidates() -> list[str]:
    here = Path(__file__).resolve()
    roots = [here.parent.parent, here.parent.parent.parent, Path.cwd()]
    out: list[str] = []
    for root in roots:
        out.extend([
            str(root / "poppler" / "Library" / "bin"),
            str(root / "dist" / "TMC_OCR" / "_internal" / "poppler" / "Library" / "bin"),
            str(root.parent / "poppler" / "Library" / "bin"),
        ])
    return out


def render_pdf(path: str, dpi: int, poppler_path: str | None = None) -> list[Image.Image]:
    dpi = int(dpi or 300)
    candidates = [poppler_path, *_auto_poppler_candidates(), None]
    last_error: Exception | None = None
    for cand in candidates:
        pp = _valid_poppler_path(cand) if cand else None
        try:
            kwargs = {"dpi": dpi}
            if pp:
                kwargs["poppler_path"] = pp
            return convert_from_path(path, **kwargs)
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(f"แปลง PDF เป็นรูปภาพไม่สำเร็จ / Poppler ใช้งานไม่ได้: {last_error}")


def preprocess(pil_img: Image.Image) -> np.ndarray:
    return enhance_for_ocr(pil_img, mode="auto", remove_color_lines=True)


def detect_grid(pil_img: Image.Image) -> dict:
    gray = cv2.cvtColor(np.array(pil_img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    h, w = inv.shape[:2]
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 35)))
    vert = cv2.morphologyEx(inv, cv2.MORPH_OPEN, vk)
    colsum = vert.sum(axis=0) / 255.0
    xs = [i for i, v in enumerate(colsum) if v > h * 0.10]
    vxs: list[int] = []
    for x in xs:
        if not vxs or x - vxs[-1] > 5:
            vxs.append(x)
        else:
            vxs[-1] = int((vxs[-1] + x) / 2)
    rows = np.where(vert.sum(axis=1) > 0)[0]
    table_top = int(rows.min()) if rows.size else int(h * 0.30)
    table_bottom = int(rows.max()) if rows.size else int(h * 0.75)
    return {"width": w, "height": h, "vxs": vxs, "table_top": table_top, "table_bottom": table_bottom, "data_top": table_top + 30, "hys": []}


def ocr_words(proc_img: np.ndarray, lang: str) -> pd.DataFrame:
    data = pytesseract.image_to_data(proc_img, lang=lang or "tha+eng", config="--oem 3 --psm 6", output_type=pytesseract.Output.DATAFRAME)
    data = data.dropna(subset=["text"])
    data["text"] = data["text"].astype(str).map(normalize_digits)
    data = data[data["text"].str.strip() != ""]
    if data.empty:
        return data
    data["conf"] = pd.to_numeric(data.get("conf", -1), errors="coerce").fillna(-1)
    data["cx"] = data["left"] + data["width"] / 2
    data["cy"] = data["top"] + data["height"] / 2
    return data.reset_index(drop=True)


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


def _extract_header_light(pil_img: Image.Image, lang: str) -> tuple[str, str]:
    w, h = pil_img.size
    crop = pil_img.crop((0, 0, w, int(h * 0.30)))
    proc_arr = enhance_for_ocr(crop, mode="auto", remove_color_lines=True)
    proc_img = Image.fromarray(proc_arr).convert("RGB") if isinstance(proc_arr, np.ndarray) else crop.convert("RGB")
    text = ""
    try:
        text = words_to_text(extract_words(proc_img, lang="en", min_confidence=0.05))
    except Exception:
        try:
            text = pytesseract.image_to_string(proc_arr, lang=lang or "tha+eng", config="--oem 3 --psm 6")
        except Exception:
            text = ""
    text = normalize_digits(text)
    po = ""
    m = re.search(r"(?:PO|P\.?O\.?|เลขที่)[^0-9]{0,20}(\d{5,})", text, flags=re.I)
    if m:
        po = m.group(1)
    date_raw = ""
    dm = DATE_RE.search(text)
    if dm:
        date_raw = dm.group(0)
    return po, date_raw


def _auto_extract_rows_paddle(pil_img: Image.Image, lang: str) -> PODocument:
    doc = PODocument()
    proc_arr = preprocess(pil_img)
    proc_img = Image.fromarray(proc_arr).convert("RGB") if isinstance(proc_arr, np.ndarray) else pil_img.convert("RGB")
    try:
        words = extract_words(proc_img, lang="en", min_confidence=0.05)
    except Exception as exc:
        doc.warnings.append(f"PaddleOCR อ่านหน้าเอกสารไม่สำเร็จ: {exc}")
        words = []
    if not words:
        doc.warnings.append("PaddleOCR ไม่พบข้อความในเอกสาร")
        return doc

    words.sort(key=lambda w: w.cy)
    heights = [w.height for w in words if w.height > 0]
    tol = max(12.0, (float(np.median(heights)) if heights else 14.0) * 0.95)
    clusters: list[list] = []
    for w in words:
        if not clusters or abs(w.cy - float(np.mean([x.cy for x in clusters[-1]]))) > tol:
            clusters.append([w])
        else:
            clusters[-1].append(w)

    lines: list[POLine] = []
    for cluster in clusters:
        cluster = sorted(cluster, key=lambda w: w.left)
        text = " ".join(w.text for w in cluster)
        if re.search(r"TOTAL|GRAND|VAT|ภาษี|รวม", text, re.I):
            continue
        nums = re.findall(r"[0-9][0-9,\.]*", normalize_digits(text))
        if len(nums) < 2:
            continue
        vals = [parse_scanned_number(n, "amount", "auto") for n in nums]
        vals = [v for v in vals if v is not None]
        if len(vals) < 2:
            continue
        desc = clean_part_description(re.sub(r"[0-9][0-9,\.]*", " ", text))
        if not desc:
            continue
        qty = vals[-3] if len(vals) >= 3 else 0.0
        price = vals[-2] if len(vals) >= 2 else 0.0
        amount = vals[-1] if len(vals) >= 1 else 0.0
        lines.append(repair_line_numbers(POLine(description_raw=desc, qty=qty, price=price, amount=amount)))

    doc.lines = lines
    total = round(sum((l.amount if l.amount > 0 else l.qty * l.price) for l in doc.lines), 2)
    doc.total = total
    doc.vat = round(total * 0.07, 2)
    doc.grand_total = round(total + doc.vat, 2)
    doc.warnings.append("ใช้ PaddleOCR อัตโนมัติ — แนะนำให้สอนตำแหน่งเพื่อความแม่นยำสูงสุด")
    return doc


def _auto_extract_rows(pil_img: Image.Image, lang: str) -> PODocument:
    return _auto_extract_rows_paddle(pil_img, lang)


def build_po_document(pil_img: Image.Image, lang: str, template: dict | None = None) -> PODocument:
    po_no, date_raw = _extract_header_light(pil_img, lang)
    if template:
        try:
            doc = build_po_document_template_v2(pil_img, lang, template)
            if doc.lines:
                doc.po_no = doc.po_no or po_no
                doc.po_date_raw = doc.po_date_raw or date_raw
                doc.po_date = doc.po_date or _iso_date(date_raw)
                doc._ocr_engine = "paddleocr"  # type: ignore[attr-defined]
                return doc
        except Exception as exc:
            doc = PODocument()
            doc.warnings.append(f"Paddle Template OCR ใช้งานไม่ได้: {exc}")
    doc = _auto_extract_rows(pil_img, lang)
    doc.po_no = doc.po_no or po_no
    doc.po_date_raw = doc.po_date_raw or date_raw
    doc.po_date = doc.po_date or _iso_date(date_raw)
    doc._ocr_engine = "paddleocr-auto"  # type: ignore[attr-defined]
    return doc
