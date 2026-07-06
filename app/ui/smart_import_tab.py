"""Smart Import tab for drag/drop PO intake."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QAbstractItemView, QCheckBox, QProgressBar, QTextEdit, QSplitter,
)

from ..context import AppContext
from .. import document_detector

COL_FILE = 0
COL_CUSTOMER = 1
COL_CONF = 2
COL_PO = 3
COL_DATE = 4
COL_STATUS = 5
COL_REASON = 6
COL_TARGET = 7
HEADERS = ["ไฟล์", "ลูกค้าที่เดาได้/เลือก", "มั่นใจ", "PO No.", "วันที่", "สถานะ", "เหตุผล", "ปลายทาง"]


class DropArea(QLabel):
    filesDropped = Signal(list)

    def __init__(self):
        super().__init__("ลากไฟล์ PO PDF มาวางตรงนี้\nหรือกดปุ่มเพิ่มไฟล์")
        self.setAlignment(Qt.AlignCenter)
        self.setAcceptDrops(True)
        self.setMinimumHeight(95)
        self.setStyleSheet(
            "QLabel{border:2px dashed #8aa0b8;border-radius:12px;"
            "padding:18px;font-size:16px;background:#f8fbff;color:#1f2937;}"
        )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                paths.append(url.toLocalFile())
        if paths:
            self.filesDropped.emit(paths)
        event.acceptProposedAction()


class DetectWorker(QThread):
    progress = Signal(int, int, str)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, paths: list[str], cfg: Any, fallback_customer: str, read_text: bool):
        super().__init__()
        self.paths = paths
        self.cfg = cfg
        self.fallback_customer = fallback_customer
        self.read_text = read_text

    def run(self):
        try:
            results = []
            total = len(self.paths)
            for i, path in enumerate(self.paths, 1):
                self.progress.emit(i, total, Path(path).name)
                res = document_detector.analyze_pdf(
                    path, self.cfg, fallback_customer=self.fallback_customer, read_text=self.read_text
                )
                results.append(res)
            self.finished.emit(results)
        except Exception as exc:
            self.failed.emit(str(exc))


class SmartImportTab(QWidget):
    def __init__(self, ctx: AppContext, on_imported=None):
        super().__init__()
        self.ctx = ctx
        self.on_imported = on_imported
        self.paths: list[str] = []
        self.results: list[document_detector.DetectionResult] = []
        self.worker: DetectWorker | None = None
        self._build()
        self.refresh_customers()

    def _build(self):
        root = QVBoxLayout(self)

        title = QLabel("Smart Import — นำเข้า PO อัตโนมัติ")
        title.setStyleSheet("font-size:20px;font-weight:700;")
        root.addWidget(title)

        desc = QLabel(
            "ลากไฟล์ PO จากที่ไหนก็ได้ → ระบบช่วยเดาลูกค้า/เลข PO → ตรวจรายการ → กดนำเข้าเข้าโฟลเดอร์ลูกค้า"
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        self.drop = DropArea()
        self.drop.filesDropped.connect(self.add_paths)
        root.addWidget(self.drop)

        controls = QHBoxLayout()
        self.btn_add = QPushButton("+ เพิ่มไฟล์ PDF")
        self.btn_add.clicked.connect(self.choose_files)
        self.btn_scan = QPushButton("🔎 วิเคราะห์/เดาลูกค้า")
        self.btn_scan.clicked.connect(self.scan)
        self.btn_import = QPushButton("📥 นำเข้าไฟล์ที่พร้อม")
        self.btn_import.clicked.connect(self.import_ready)
        self.btn_clear = QPushButton("ล้างรายการ")
        self.btn_clear.clicked.connect(self.clear_all)
        controls.addWidget(self.btn_add)
        controls.addWidget(self.btn_scan)
        controls.addWidget(self.btn_import)
        controls.addWidget(self.btn_clear)
        controls.addStretch()
        root.addLayout(controls)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("ถ้าเดาไม่ได้ ให้ใช้ลูกค้า:"))
        self.cmb_fallback = QComboBox()
        self.cmb_fallback.setMinimumWidth(240)
        opts.addWidget(self.cmb_fallback)
        self.chk_ocr = QCheckBox("อ่านหน้าแรกเพื่อช่วยเดา (แม่นขึ้นแต่ช้ากว่า)")
        self.chk_ocr.setChecked(True)
        opts.addWidget(self.chk_ocr)
        self.chk_rename = QCheckBox("ถ้าชื่อไฟล์ซ้ำ ให้เปลี่ยนชื่อให้อัตโนมัติ")
        self.chk_rename.setChecked(True)
        opts.addWidget(self.chk_rename)
        opts.addStretch()
        root.addLayout(opts)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setStyleSheet(
            "QTableWidget{background:#2b2b2b;color:#f3f4f6;gridline-color:#555;}"
            "QHeaderView::section{background:#3a3a3a;color:#ffffff;}"
        )
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(COL_FILE, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(COL_CUSTOMER, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(COL_REASON, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_TARGET, QHeaderView.Stretch)
        self.table.cellChanged.connect(self._cell_changed)
        self.table.itemSelectionChanged.connect(self._show_preview)
        split.addWidget(self.table)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setStyleSheet(
            "QTextEdit{background:#1f2937;color:#f9fafb;border:1px solid #4b5563;}"
        )
        self.preview.setPlaceholderText("เลือกแถวเพื่อดูข้อความตัวอย่างจากหน้าแรก")
        split.addWidget(self.preview)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

    def refresh_customers(self):
        current = self.cmb_fallback.currentText()
        self.cmb_fallback.blockSignals(True)
        self.cmb_fallback.clear()
        self.cmb_fallback.addItem("-- ไม่ระบุ --")
        try:
            names = self.ctx.customers()
        except Exception:
            names = document_detector.customer_names(self.ctx.cfg)
        self.cmb_fallback.addItems(names)
        if current:
            idx = self.cmb_fallback.findText(current)
            if idx >= 0:
                self.cmb_fallback.setCurrentIndex(idx)
        self.cmb_fallback.blockSignals(False)

    def choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "เลือกไฟล์ PO PDF", "", "PDF (*.pdf *.PDF)")
        if files:
            self.add_paths(files)

    def add_paths(self, paths: list[str]):
        added = 0
        seen = set(self.paths)
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                for pattern in ("*.pdf", "*.PDF"):
                    for f in sorted(p.rglob(pattern)):
                        s = str(f)
                        if s not in seen:
                            self.paths.append(s); seen.add(s); added += 1
            else:
                s = str(p)
                if s not in seen:
                    self.paths.append(s); seen.add(s); added += 1
        if added:
            self._populate_pending_rows()
            self.summary.setText(f"เพิ่มไฟล์ใหม่ {added} ไฟล์ — กด 'วิเคราะห์/เดาลูกค้า' เพื่อเริ่มตรวจ")

    def _populate_pending_rows(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for path in self.paths:
            row = self.table.rowCount()
            self.table.insertRow(row)
            p = Path(path)
            vals = [p.name, "", "", "", "", "รอวิเคราะห์", "", ""]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(str(val))
                if col not in (COL_CUSTOMER, COL_PO, COL_DATE):
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)

    def scan(self):
        if not self.paths:
            QMessageBox.information(self, "Smart Import", "กรุณาเพิ่มไฟล์ PDF ก่อน")
            return
        if self.worker and self.worker.isRunning():
            return
        fallback = self.cmb_fallback.currentText()
        if fallback.startswith("--"):
            fallback = ""
        self.progress.setVisible(True)
        self.progress.setRange(0, len(self.paths))
        self.progress.setValue(0)
        self.btn_scan.setEnabled(False)
        self.worker = DetectWorker(self.paths, self.ctx.cfg, fallback, self.chk_ocr.isChecked())
        self.worker.progress.connect(self._scan_progress)
        self.worker.finished.connect(self._scan_finished)
        self.worker.failed.connect(self._scan_failed)
        self.worker.start()

    def _scan_progress(self, i: int, total: int, name: str):
        self.progress.setMaximum(total)
        self.progress.setValue(i)
        self.summary.setText(f"กำลังวิเคราะห์ {i}/{total}: {name}")

    def _scan_failed(self, message: str):
        self.progress.setVisible(False)
        self.btn_scan.setEnabled(True)
        QMessageBox.warning(self, "Smart Import", f"วิเคราะห์ไฟล์ไม่สำเร็จ:\n{message}")

    def _scan_finished(self, results: list):
        self.results = results
        self.progress.setVisible(False)
        self.btn_scan.setEnabled(True)
        self._render_results()
        self._update_summary()

    def _item(self, text: str, editable: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(str(text or ""))
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        return item

    def _render_results(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for res in self.results:
            row = self.table.rowCount()
            self.table.insertRow(row)
            vals = [
                res.file_name,
                res.detected_customer,
                f"{res.confidence}%" if res.confidence else "",
                res.po_no,
                res.po_date,
                res.status,
                res.reason,
                res.target_path,
            ]
            for col, val in enumerate(vals):
                editable = col in (COL_CUSTOMER, COL_PO, COL_DATE)
                item = self._item(val, editable=editable)
                self.table.setItem(row, col, item)
            self._color_row(row, res.status)
        self.table.blockSignals(False)

    def _color_row(self, row: int, status: str):
        """Apply readable status colors to a whole row.

        The app usually runs in dark mode, but these status backgrounds are light.
        Without setting the foreground explicitly, Qt may keep white text, making
        yellow/green rows almost unreadable. This method always sets a high
        contrast foreground for each status row.
        """
        if status in ("พร้อมนำเข้า",):
            bg = QColor(225, 248, 232)
            fg = QColor(20, 83, 45)
        elif status in ("ควรตรวจ", "ชื่อซ้ำ"):
            bg = QColor(255, 246, 204)
            fg = QColor(92, 64, 0)
        elif status in ("ต้องเลือกลูกค้า", "ไม่พบไฟล์", "ผิดพลาด"):
            bg = QColor(255, 226, 226)
            fg = QColor(127, 29, 29)
        elif status == "นำเข้าสำเร็จ":
            bg = QColor(220, 245, 255)
            fg = QColor(12, 74, 110)
        else:
            bg = QColor(245, 245, 245)
            fg = QColor(31, 41, 55)
        for c in range(self.table.columnCount()):
            it = self.table.item(row, c)
            if it:
                it.setBackground(QBrush(bg))
                it.setForeground(QBrush(fg))
    def _cell_changed(self, row: int, col: int):
        if row < 0 or row >= len(self.results):
            return
        res = self.results[row]
        if col == COL_CUSTOMER:
            res.detected_customer = self.table.item(row, col).text().strip() if self.table.item(row, col) else ""
            if res.detected_customer:
                target = document_detector.target_for_customer(self.ctx.cfg, res.detected_customer, res.file_name)
                res.target_path = str(target)
                if target.exists():
                    res.status = "ชื่อซ้ำ"
                elif res.confidence >= 55:
                    res.status = "พร้อมนำเข้า"
                else:
                    res.status = "ควรตรวจ"
            else:
                res.status = "ต้องเลือกลูกค้า"
            self.table.blockSignals(True)
            self.table.item(row, COL_STATUS).setText(res.status)
            self.table.item(row, COL_TARGET).setText(res.target_path)
            self._color_row(row, res.status)
            self.table.blockSignals(False)
            self._update_summary()
        elif col == COL_PO:
            res.po_no = self.table.item(row, col).text().strip() if self.table.item(row, col) else ""
        elif col == COL_DATE:
            res.po_date = self.table.item(row, col).text().strip() if self.table.item(row, col) else ""

    def _show_preview(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if not rows or rows[0] >= len(self.results):
            self.preview.clear()
            return
        res = self.results[rows[0]]
        txt = res.text_preview or "ไม่มีข้อความตัวอย่าง หรือไฟล์เป็นภาพสแกนที่ยังอ่านไม่ได้ในขั้นตอน preview"
        self.preview.setPlainText(txt)

    def import_ready(self):
        if not self.results:
            QMessageBox.information(self, "Smart Import", "กรุณาวิเคราะห์ไฟล์ก่อน")
            return
        selected_rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if selected_rows:
            candidates = [self.results[i] for i in selected_rows if i < len(self.results)]
        else:
            candidates = [r for r in self.results if r.detected_customer and r.status in ("พร้อมนำเข้า", "ควรตรวจ", "ชื่อซ้ำ")]

        if not candidates:
            QMessageBox.information(self, "Smart Import", "ยังไม่มีไฟล์ที่พร้อมนำเข้า กรุณาเลือก/แก้ลูกค้าก่อน")
            return

        answer = QMessageBox.question(
            self,
            "ยืนยันนำเข้า",
            f"ต้องการนำเข้า {len(candidates)} ไฟล์เข้าโฟลเดอร์ PO ของลูกค้าหรือไม่?\n\n"
            "ถ้าไม่ได้เลือกแถว ระบบจะนำเข้าเฉพาะไฟล์ที่พร้อม/ควรตรวจเท่านั้น",
        )
        if answer != QMessageBox.Yes:
            return

        ok_count = 0
        failed = []
        for res in candidates:
            ok, msg, target = document_detector.import_detection(
                res, self.ctx.cfg, rename_duplicate=self.chk_rename.isChecked()
            )
            if ok:
                ok_count += 1
                res.status = "นำเข้าสำเร็จ"
                res.target_path = target
            else:
                failed.append(f"{res.file_name}: {msg}")
                res.status = "ผิดพลาด"
                res.reason = msg
        self._render_results()
        self._update_summary()
        if self.on_imported:
            try:
                self.on_imported()
            except Exception:
                pass
        msg = f"นำเข้าสำเร็จ {ok_count} ไฟล์"
        if failed:
            msg += "\n\nไฟล์ที่ไม่สำเร็จ:\n" + "\n".join(failed[:10])
        QMessageBox.information(self, "Smart Import", msg)

    def _update_summary(self):
        if not self.results:
            self.summary.setText(f"มีไฟล์รอวิเคราะห์ {len(self.paths)} ไฟล์")
            return
        counts = {}
        for r in self.results:
            counts[r.status] = counts.get(r.status, 0) + 1
        parts = [f"{k}: {v}" for k, v in sorted(counts.items())]
        self.summary.setText(" | ".join(parts))

    def clear_all(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Smart Import", "ระบบกำลังวิเคราะห์ไฟล์อยู่ กรุณารอให้เสร็จก่อน")
            return
        self.paths.clear()
        self.results.clear()
        self.table.setRowCount(0)
        self.preview.clear()
        self.summary.clear()
