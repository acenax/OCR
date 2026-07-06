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
