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

# === PHASE2 MAPPING MEMORY PIPELINE PATCH ===
_phase2_original_process_pdf = process_pdf


def process_pdf(pdf_path: str, customer: str, cfg: Config, matcher: ProductMatcher | None) -> PODocument:
    """Phase 2 wrapper: run original OCR pipeline, then apply remembered manual mappings."""
    doc = _phase2_original_process_pdf(pdf_path, customer, cfg, matcher)
    try:
        from . import mapping_memory
        mapping_memory.apply_to_document(customer, doc, override_fuzzy=True)
    except Exception as exc:
        try:
            doc.warnings.append(f"อ่านประวัติการแก้ไขสินค้าไม่สำเร็จ: {exc}")
        except Exception:
            pass
    return doc

# === PHASE5 OCR CACHE PATCH ===
try:
    from . import ocr_cache as _phase5_ocr_cache
    if not getattr(process_pdf, "_phase5_cache_wrapped", False):
        _phase5_original_process_pdf = process_pdf
        def _phase5_process_pdf_cached(pdf_path, customer, cfg, matcher):
            try:
                cached = _phase5_ocr_cache.load_cached_document(pdf_path, customer, cfg, matcher)
                if cached is not None:
                    cached.source_pdf = str(pdf_path)
                    cached.customer = customer
                    setattr(cached, "cache_hit", True)
                    return cached
            except Exception:
                pass
            doc = _phase5_original_process_pdf(pdf_path, customer, cfg, matcher)
            try:
                _phase5_ocr_cache.save_document(pdf_path, customer, cfg, matcher, doc)
                setattr(doc, "cache_hit", False)
            except Exception:
                pass
            return doc
        _phase5_process_pdf_cached._phase5_cache_wrapped = True
        process_pdf = _phase5_process_pdf_cached
except Exception as _phase5_cache_error:
    print("PHASE5 OCR CACHE PATCH disabled:", _phase5_cache_error)
# === END PHASE5 OCR CACHE PATCH ===

# === PHASE9 AMOUNT/TOTAL REPAIR PIPELINE PATCH ===
_phase9_original_process_pdf = process_pdf

def process_pdf(pdf_path: str, customer: str, cfg: Config, matcher: ProductMatcher | None) -> PODocument:
    doc = _phase9_original_process_pdf(pdf_path, customer, cfg, matcher)
    try:
        from . import amount_repair
        amount_repair.repair_document(doc, update_header=True)
    except Exception as exc:
        try:
            doc.warnings.append(f"ซ่อมยอดเงิน/ราคาอัตโนมัติไม่สำเร็จ: {exc}")
        except Exception:
            pass
    return doc

# === PHASE10 OCR STABILITY NORMALIZER ===
try:
    from app.ocr_stability import normalize_po_document as _phase10_normalize_po_document
except Exception:
    try:
        from .ocr_stability import normalize_po_document as _phase10_normalize_po_document
    except Exception:
        _phase10_normalize_po_document = None

try:
    if _phase10_normalize_po_document is not None and '_phase10_orig_process_pdf' not in globals():
        _phase10_orig_process_pdf = process_pdf
        def process_pdf(*args, **kwargs):
            doc = _phase10_orig_process_pdf(*args, **kwargs)
            return _phase10_normalize_po_document(doc)
except Exception as _phase10_exc:
    print('Phase10 pipeline normalizer skipped:', _phase10_exc)
# === END PHASE10 OCR STABILITY NORMALIZER ===

# === PHASE12 CLEANUP DELETE ARABIC PATCH ===
try:
    from .arabic_digits import normalize_obj_digits as _phase12_norm_doc
    # Patch likely public functions that return PODocument.
    for _name, _fn in list(globals().items()):
        if callable(_fn) and _name.lower() in {"process_pdf", "process_po", "read_pdf", "parse_pdf", "ocr_pdf", "run_pipeline"}:
            def _make_wrapper(fn):
                def _wrapped(*args, **kwargs):
                    res = fn(*args, **kwargs)
                    try:
                        if isinstance(res, list):
                            for x in res: _phase12_norm_doc(x)
                        else:
                            _phase12_norm_doc(res)
                    except Exception:
                        pass
                    return res
                return _wrapped
            globals()[_name] = _make_wrapper(_fn)
except Exception:
    pass
