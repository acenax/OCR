"""Orchestrates reading one PO PDF into a fully-matched PODocument."""
from __future__ import annotations

import re
from pathlib import Path

from . import ocr, template
from .config import Config
from .matcher import ProductMatcher
from .models import PODocument


def customer_folders(root: str, po_subfolder: str) -> list[str]:
    """Customer = any sub-folder of root that contains a PO sub-folder."""
    out = []
    r = Path(root)
    if not r.exists():
        return out
    for child in sorted(r.iterdir()):
        if child.is_dir() and (child / po_subfolder).exists():
            out.append(child.name)
    return out


def product_file_for(root: str, customer: str, product_subfolder: str) -> str:
    d = Path(root) / customer / product_subfolder
    if d.exists():
        for f in d.glob("*.xlsx"):
            if not f.name.startswith("~$"):
                return str(f)
    return ""


def po_number_from_name(pdf_path: str) -> str:
    stem = Path(pdf_path).stem
    m = re.search(r"(\d{6,})", stem)
    return m.group(1) if m else stem.strip()


def process_pdf(pdf_path: str, customer: str, cfg: Config,
                matcher: ProductMatcher | None) -> PODocument:
    """Full pipeline for one PDF: OCR -> match -> return document."""
    tmpl = template.load(customer)
    images = ocr.render_pdf(pdf_path, cfg["dpi"], cfg["poppler_path"])
    if not images:
        doc = PODocument(source_pdf=pdf_path, customer=customer)
        doc.warnings.append("แปลง PDF เป็นรูปภาพไม่สำเร็จ")
        return doc

    # read EVERY page and concatenate line items (POs can span many pages)
    page_docs = [ocr.build_po_document(im, cfg["ocr_lang"], tmpl) for im in images]
    doc = page_docs[0]                       # header (date/anchors) comes from page 1
    doc.source_pdf = pdf_path
    doc.customer = customer
    doc.po_no = po_number_from_name(pdf_path)

    all_lines = []
    for pdoc in page_docs:
        all_lines.extend(pdoc.lines)
    doc.lines = all_lines

    # merge warnings across pages; drop the "no items" note if any page had items
    warns = []
    for pdoc in page_docs:
        for w in pdoc.warnings:
            if w not in warns:
                warns.append(w)
    if all_lines:
        warns = [w for w in warns if "ไม่พบรายการสินค้า" not in w]
    doc.warnings = warns

    # totals across all pages (qty*price / amount are the reliable per-line values)
    if len(images) > 1:
        total = round(sum((l.amount or l.qty * l.price) for l in all_lines), 2)
        doc.total = total
        doc.vat = round(total * 0.07, 2)
        doc.grand_total = round(total + doc.vat, 2)

    # auto-learn the layout for a new customer (grid-detected pages only)
    if not template.exists(customer) and getattr(doc, "_anchors_frac", None) \
            and not getattr(doc, "_used_template", False):
        template.save(customer, doc._anchors_frac, doc._header_bottom)  # type: ignore[attr-defined]

    if matcher is not None:
        for line in doc.lines:
            # match on DESCRIPTION first (real part number), fall back to code
            res = matcher.match(line.description_raw)
            if res.status == "no_match" and line.product_code_raw:
                alt = matcher.match(line.product_code_raw)
                if alt.status != "no_match":
                    res = alt
            line.tmc_code = res.tmc_code
            line.matched_name = res.matched_name
            line.match_score = round(res.score, 1)
            line.match_status = res.status
    return doc


def relearn_layout(doc: PODocument):
    """Persist the layout detected in this document as the customer's template."""
    if getattr(doc, "_anchors_frac", None):
        template.save(doc.customer, doc._anchors_frac, doc._header_bottom)  # type: ignore[attr-defined]
        return True
    return False
