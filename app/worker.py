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
