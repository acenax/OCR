"""Background OCR worker so the UI stays responsive."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from . import pipeline
from .context import AppContext
from .models import PODocument


class OcrWorker(QThread):
    progress = Signal(int, int, str)      # done, total, filename
    one_done = Signal(object)             # PODocument
    finished_all = Signal()
    error = Signal(str)

    def __init__(self, ctx: AppContext, customer: str, pdf_paths: list[str]):
        super().__init__()
        self.ctx = ctx
        self.customer = customer
        self.pdf_paths = pdf_paths

    def run(self):
        total = len(self.pdf_paths)
        for i, path in enumerate(self.pdf_paths, 1):
            try:
                self.progress.emit(i, total, path)
                doc: PODocument = pipeline.process_pdf(
                    path, self.customer, self.ctx.cfg, self.ctx.matcher)
                self.one_done.emit(doc)
            except Exception as e:  # keep going on the next file
                self.error.emit(f"{path}\n{e}")
        self.finished_all.emit()

# === PHASE2 WORKER ERROR PATCH ===
def _phase2_ocr_worker_run(self):
    """Run OCR and aggregate errors to avoid multiple popup dialogs."""
    from pathlib import Path
    total = len(self.pdf_paths)
    errors = []
    for i, path in enumerate(self.pdf_paths, 1):
        try:
            self.progress.emit(i, total, path)
            doc = pipeline.process_pdf(path, self.customer, self.ctx.cfg, self.ctx.matcher)
            self.one_done.emit(doc)
        except Exception as e:
            errors.append(f"{Path(path).name}\n{e}")
    if errors:
        preview = "\n\n".join(errors[:5])
        if len(errors) > 5:
            preview += f"\n\n...และอีก {len(errors) - 5} ไฟล์"
        self.error.emit(f"อ่าน OCR ไม่สำเร็จบางไฟล์ ({len(errors)} ไฟล์)\n\n{preview}")
    self.finished_all.emit()

try:
    OcrWorker.run = _phase2_ocr_worker_run
except Exception:
    pass

# === PHASE5 QUEUE STATUS WORKER PATCH ===
try:
    from pathlib import Path as _Phase5Path
    from . import queue_status as _phase5_queue_status
    from . import audit_log as _phase5_audit_log
    def _phase5_doc_needs_review(doc):
        try:
            if getattr(doc, "warnings", None):
                return True
            for line in getattr(doc, "lines", []) or []:
                if not getattr(line, "tmc_code", "") or not getattr(line, "stock_group_code", ""):
                    return True
                if float(getattr(line, "qty", 0) or 0) <= 0 or float(getattr(line, "price", 0) or 0) <= 0:
                    return True
                if str(getattr(line, "match_status", "")) in ("fuzzy", "no_match", ""):
                    return True
        except Exception:
            return True
        return False
    def _phase5_ocr_worker_run(self):
        total = len(self.pdf_paths)
        errors = []
        for i, path in enumerate(self.pdf_paths, 1):
            try:
                _phase5_queue_status.set_status(path, "processing", customer=self.customer, message=f"กำลังอ่าน OCR {i}/{total}")
                self.progress.emit(i, total, path)
                doc = pipeline.process_pdf(path, self.customer, self.ctx.cfg, self.ctx.matcher)
                cache_hit = bool(getattr(doc, "cache_hit", False))
                status = "need_review" if _phase5_doc_needs_review(doc) else "ready"
                message = "มีรายการที่ต้องตรวจ/แก้ก่อน Export" if status == "need_review" else "พร้อม Export"
                _phase5_queue_status.set_status(path, status, customer=self.customer, message=message, item_count=int(getattr(doc, "item_count", 0)), warnings=list(getattr(doc, "warnings", []) or []), cache_hit=cache_hit)
                _phase5_audit_log.log_event("ocr_done", customer=self.customer, path=str(path), item_count=int(getattr(doc, "item_count", 0)), status=status, cache_hit=cache_hit)
                self.one_done.emit(doc)
            except Exception as e:
                msg = str(e)
                _phase5_queue_status.set_status(path, "failed", customer=self.customer, message=msg)
                _phase5_audit_log.log_event("ocr_failed", customer=self.customer, path=str(path), error=msg)
                errors.append(f"{_Phase5Path(path).name}\n{msg}")
        if errors:
            preview = "\n\n".join(errors[:5])
            if len(errors) > 5:
                preview += f"\n\n...และอีก {len(errors) - 5} ไฟล์"
            self.error.emit(f"อ่าน OCR ไม่สำเร็จบางไฟล์ ({len(errors)} ไฟล์)\n\n{preview}")
        self.finished_all.emit()
    OcrWorker.run = _phase5_ocr_worker_run
except Exception as _phase5_worker_error:
    print("PHASE5 QUEUE STATUS WORKER PATCH disabled:", _phase5_worker_error)
# === END PHASE5 QUEUE STATUS WORKER PATCH ===
