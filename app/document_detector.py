"""Smart PDF intake and customer/PO detection helpers.

This module is intentionally conservative: it does not overwrite existing PO files,
and it only auto-selects a customer when the confidence is reasonable. The operator
can still override the customer in the Smart Import tab before importing.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import json
import re
import shutil


@dataclass
class DetectionResult:
    source_path: str
    file_name: str
    is_pdf: bool = True
    detected_customer: str = ""
    confidence: int = 0
    po_no: str = ""
    po_date: str = ""
    reason: str = ""
    status: str = "รอตรวจ"
    target_path: str = ""
    text_preview: str = ""


_WORD_RE = re.compile(r"[A-Za-z0-9ก-๙]+")
_PO_PATTERNS = [
    re.compile(r"(?:P\.?O\.?|PO|PURCHASE\s*ORDER)\s*(?:NO\.?|NUMBER|#|:)?\s*([A-Z0-9][A-Z0-9_\-/]{4,})", re.I),
    re.compile(r"(?:เลขที่|เลขที่เอกสาร|เลขที่ใบสั่งซื้อ|ใบสั่งซื้อ)\s*[:#]?\s*([A-Z0-9][A-Z0-9_\-/]{4,})", re.I),
    re.compile(r"\b([A-Z]{0,4}\d{6,}[A-Z0-9_\-/]*)\b", re.I),
]
_DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b"),
    re.compile(r"\b(\d{4}[/-]\d{1,2}[/-]\d{1,2})\b"),
]


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for m in _WORD_RE.finditer(str(text).lower()):
        t = m.group(0).strip()
        if len(t) >= 2:
            out.add(t)
    return out


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _cfg_get(cfg: Any, key: str, default: str = "") -> str:
    try:
        return str(cfg.get(key, default))
    except Exception:
        try:
            return str(cfg[key])
        except Exception:
            return default


def customer_names(cfg: Any) -> list[str]:
    root = Path(_cfg_get(cfg, "root_folder", ""))
    if not root.exists():
        return []
    names = []
    for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
        if p.is_dir() and not p.name.startswith((".", "_")):
            names.append(p.name)
    return names


def _possible_poppler_path(cfg: Any) -> str | None:
    raw = _cfg_get(cfg, "poppler_path", "").strip()
    if raw and Path(raw).exists():
        return raw
    root = Path.cwd()
    candidates = [
        root / "poppler" / "Library" / "bin",
        root.parent / "poppler" / "Library" / "bin",
        Path("C:/poppler/Library/bin"),
        Path("C:/Program Files/poppler/Library/bin"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _configure_tesseract(cfg: Any) -> None:
    raw = _cfg_get(cfg, "tesseract_path", "").strip()
    if not raw:
        return
    try:
        import pytesseract  # type: ignore
        p = Path(raw)
        if p.is_dir():
            exe = p / "tesseract.exe"
            if exe.exists():
                pytesseract.pytesseract.tesseract_cmd = str(exe)
        elif p.exists():
            pytesseract.pytesseract.tesseract_cmd = str(p)
    except Exception:
        pass


def extract_text_preview(pdf_path: str | Path, cfg: Any, max_chars: int = 3500) -> str:
    """Try to extract searchable text; fallback to OCR first page if needed."""
    p = Path(pdf_path)
    text = ""

    # First try PDF text extraction if pypdf/PyPDF2 happens to be installed.
    for module_name in ("pypdf", "PyPDF2"):
        try:
            mod = __import__(module_name)
            reader_cls = getattr(mod, "PdfReader", None)
            if reader_cls is None and hasattr(mod, "PdfFileReader"):
                reader_cls = getattr(mod, "PdfFileReader")
            if reader_cls is None:
                continue
            reader = reader_cls(str(p))
            pages = getattr(reader, "pages", [])
            if pages:
                page = pages[0]
                if hasattr(page, "extract_text"):
                    text = page.extract_text() or ""
                elif hasattr(page, "extractText"):
                    text = page.extractText() or ""
            if text.strip():
                return _normalize(text)[:max_chars]
        except Exception:
            pass

    # Fallback: render first page with pdf2image and run Tesseract.
    try:
        from pdf2image import convert_from_path  # type: ignore
        import pytesseract  # type: ignore
        _configure_tesseract(cfg)
        kwargs: dict[str, Any] = {
            "dpi": 160,
            "first_page": 1,
            "last_page": 1,
            "thread_count": 1,
        }
        pp = _possible_poppler_path(cfg)
        if pp:
            kwargs["poppler_path"] = pp
        pages = convert_from_path(str(p), **kwargs)
        if pages:
            lang = _cfg_get(cfg, "ocr_lang", "tha+eng") or "tha+eng"
            text = pytesseract.image_to_string(pages[0], lang=lang)
            return _normalize(text)[:max_chars]
    except Exception:
        return ""

    return ""


def detect_po_number(text: str, filename: str = "") -> str:
    hay = f"{filename}\n{text or ''}"
    for pat in _PO_PATTERNS:
        m = pat.search(hay)
        if m:
            val = (m.group(1) or "").strip().strip("._- ")
            # Avoid pure date-looking values.
            if val and not re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", val):
                return val
    return ""


def detect_po_date(text: str, filename: str = "") -> str:
    hay = f"{filename}\n{text or ''}"
    for pat in _DATE_PATTERNS:
        m = pat.search(hay)
        if m:
            return (m.group(1) or "").strip()
    return ""


def _score_customer(customer: str, text: str, filename: str) -> tuple[int, str]:
    cust_lower = customer.lower()
    hay_text = (text or "").lower()
    hay_file = (filename or "").lower()
    score = 0
    reasons = []

    if cust_lower and cust_lower in hay_file:
        score += 65
        reasons.append("ชื่อลูกค้าอยู่ในชื่อไฟล์")
    if cust_lower and cust_lower in hay_text:
        score += 55
        reasons.append("พบชื่อลูกค้าในเอกสาร")

    customer_tokens = _tokens(customer)
    file_tokens = _tokens(filename)
    text_tokens = _tokens(text)

    common_file = customer_tokens & file_tokens
    common_text = customer_tokens & text_tokens
    if common_file:
        score += min(40, len(common_file) * 14)
        reasons.append("คำในชื่อลูกค้าตรงกับชื่อไฟล์")
    if common_text:
        score += min(35, len(common_text) * 10)
        reasons.append("คำในชื่อลูกค้าตรงกับข้อความในเอกสาร")

    if len(customer.replace(" ", "")) <= 3 and cust_lower not in hay_file and cust_lower not in hay_text:
        score = min(score, 30)

    return min(score, 100), ", ".join(reasons)


def detect_customer(pdf_path: str | Path, cfg: Any, text: str = "") -> tuple[str, int, str]:
    p = Path(pdf_path)
    names = customer_names(cfg)
    if not names:
        return "", 0, "ยังไม่มีโฟลเดอร์ลูกค้าในระบบ"

    ranked = []
    for name in names:
        sc, reason = _score_customer(name, text, p.name)
        ranked.append((sc, name, reason))
    ranked.sort(reverse=True)
    best_score, best_name, best_reason = ranked[0]

    if best_score >= 55:
        return best_name, int(best_score), best_reason or "เดาจากชื่อไฟล์/ข้อความ"

    if len(names) == 1:
        return names[0], 35, "มีลูกค้าในระบบเพียงรายเดียว กรุณาตรวจสอบก่อนนำเข้า"

    return "", int(best_score), "ยังเดาลูกค้าไม่มั่นใจ กรุณาเลือกเอง"


def target_for_customer(cfg: Any, customer: str, filename: str) -> Path:
    root = Path(_cfg_get(cfg, "root_folder", ""))
    po_sub = _cfg_get(cfg, "po_subfolder", "PO") or "PO"
    return root / customer / po_sub / filename


def unique_target_path(path: str | Path) -> Path:
    p = Path(path)
    if not p.exists():
        return p
    stem = p.stem
    suffix = p.suffix
    for i in range(1, 1000):
        cand = p.with_name(f"{stem}_{i:02d}{suffix}")
        if not cand.exists():
            return cand
    raise RuntimeError(f"ไม่สามารถสร้างชื่อไฟล์ใหม่ได้: {p}")


def analyze_pdf(pdf_path: str | Path, cfg: Any, fallback_customer: str = "", read_text: bool = True) -> DetectionResult:
    p = Path(pdf_path)
    res = DetectionResult(source_path=str(p), file_name=p.name, is_pdf=(p.suffix.lower() == ".pdf"))
    if not p.exists():
        res.status = "ไม่พบไฟล์"
        res.reason = "ไฟล์ไม่มีอยู่จริง"
        return res
    if not p.is_file() or p.suffix.lower() != ".pdf":
        res.is_pdf = False
        res.status = "ข้าม"
        res.reason = "ไม่ใช่ไฟล์ PDF"
        return res

    text = extract_text_preview(p, cfg) if read_text else ""
    res.text_preview = text[:500]
    customer, conf, reason = detect_customer(p, cfg, text)
    if not customer and fallback_customer:
        customer = fallback_customer
        conf = max(conf, 25)
        reason = "ใช้ลูกค้าที่เลือกไว้เป็นค่าเริ่มต้น"

    res.detected_customer = customer
    res.confidence = int(conf)
    res.reason = reason
    res.po_no = detect_po_number(text, p.stem)
    res.po_date = detect_po_date(text, p.stem)

    if not customer:
        res.status = "ต้องเลือกลูกค้า"
        return res

    target = target_for_customer(cfg, customer, p.name)
    res.target_path = str(target)
    if target.exists():
        res.status = "ชื่อซ้ำ"
        res.reason = (res.reason + "; " if res.reason else "") + "มีไฟล์ชื่อนี้ในโฟลเดอร์ลูกค้าแล้ว"
    elif conf >= 55:
        res.status = "พร้อมนำเข้า"
    else:
        res.status = "ควรตรวจ"
    return res


def import_detection(result: DetectionResult, cfg: Any, rename_duplicate: bool = True) -> tuple[bool, str, str]:
    if not result.is_pdf:
        return False, "ไม่ใช่ไฟล์ PDF", ""
    if not result.detected_customer:
        return False, "ยังไม่ได้เลือกลูกค้า", ""
    src = Path(result.source_path)
    if not src.exists():
        return False, "ไม่พบไฟล์ต้นทาง", ""
    target = target_for_customer(cfg, result.detected_customer, src.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if rename_duplicate:
            target = unique_target_path(target)
        else:
            return False, "มีไฟล์ชื่อนี้อยู่แล้ว", str(target)
    shutil.copy2(src, target)
    return True, "นำเข้าสำเร็จ", str(target)


def result_to_dict(res: DetectionResult) -> dict[str, Any]:
    return asdict(res)


def save_intake_report(results: list[DetectionResult], out_path: str | Path) -> str:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = [result_to_dict(r) for r in results]
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)
