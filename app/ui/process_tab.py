"""Tab 1: process PO PDFs -> editable line items -> save Excel + monthly summary."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox,
    QPushButton, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QLineEdit, QFileDialog, QMessageBox, QProgressBar, QGroupBox, QAbstractItemView,
    QHeaderView, QSplitter,
)

from .. import exporter, pipeline, customers
from ..context import AppContext
from ..models import PODocument, POLine
from ..worker import OcrWorker
from .widgets import SearchCombo, TmcCombo

COLS = ["ลำดับ", "รหัสสินค้า (OCR)", "ชื่อสินค้า (OCR)",
        "tmc_code", "ชื่อสินค้าที่จับคู่ (TMC)", "", "จำนวน", "ราคา", "ผลจับคู่"]
C_ITEM, C_CODE, C_DESC, C_TMC, C_MATCH, C_STOCK, C_QTY, C_PRICE, C_STATUS = range(9)


class ProcessTab(QWidget):
    def __init__(self, ctx: AppContext, on_saved=None):
        super().__init__()
        self.ctx = ctx
        self.on_saved = on_saved
        self.docs: dict[str, PODocument] = {}     # pdf_path -> document
        self.current_path: str = ""
        self.worker: OcrWorker | None = None
        self._build()
        self.refresh_customers()

    # ---------------- UI ----------------
    def _build(self):
        root = QHBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        # left panel
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("<b>ลูกค้า (Customer)</b>"))
        self.cmb_customer = QComboBox()
        self.cmb_customer.currentTextChanged.connect(self._customer_changed)
        lv.addWidget(self.cmb_customer)

        row = QHBoxLayout()
        b_folder = QPushButton("เลือกโฟลเดอร์ PO")
        b_folder.clicked.connect(self.choose_folder)
        b_files = QPushButton("เลือกไฟล์ PDF")
        b_files.clicked.connect(self.choose_files)
        row.addWidget(b_folder)
        row.addWidget(b_files)
        lv.addLayout(row)

        b_import_po = QPushButton("📥 นำเข้าไฟล์ PO เข้าโฟลเดอร์ลูกค้า")
        b_import_po.setToolTip("คัดลอกไฟล์ PDF เข้าโฟลเดอร์ PO ของลูกค้า (สร้างโฟลเดอร์ให้อัตโนมัติ)")
        b_import_po.clicked.connect(self.import_po)
        lv.addWidget(b_import_po)

        lv.addWidget(QLabel("ไฟล์ PO:"))
        self.lst = QListWidget()
        self.lst.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.lst.currentItemChanged.connect(self._pdf_selected)
        lv.addWidget(self.lst, 1)

        self.btn_delete_po = QPushButton("🗑 เอาไฟล์ PO ออกจากรายการ")
        self.btn_delete_po.setToolTip("ย้ายไฟล์ไป _REMOVED_FROM_OCR โดยไม่ลบไฟล์จริงออกจากเครื่อง")
        self.btn_delete_po.clicked.connect(self.delete_selected_po_files)
        lv.addWidget(self.btn_delete_po)

        self.btn_ocr = QPushButton("▶ อ่านเอกสาร (OCR)")
        self.btn_ocr.clicked.connect(self.run_ocr)
        lv.addWidget(self.btn_ocr)
        self.btn_teach = QPushButton("🖼️ สอนตำแหน่ง (ลากกรอบ)")
        self.btn_teach.setToolTip("เปิดเอกสารแล้วลากกรอบครอบคอลัมน์ต่าง ๆ ด้วยตนเอง")
        self.btn_teach.clicked.connect(self.teach_layout)
        lv.addWidget(self.btn_teach)
        self.btn_relearn = QPushButton("↻ เรียนรู้ตำแหน่งอัตโนมัติ (ไฟล์นี้)")
        self.btn_relearn.setToolTip("บันทึกตำแหน่งคอลัมน์ที่ตรวจจับอัตโนมัติจากไฟล์นี้")
        self.btn_relearn.clicked.connect(self.relearn)
        lv.addWidget(self.btn_relearn)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lv.addWidget(self.progress)
        splitter.addWidget(left)

        # right panel
        right = QWidget()
        rv = QVBoxLayout(right)

        hdr = QGroupBox("ข้อมูลหัวเอกสาร (Header)")
        g = QGridLayout(hdr)
        self.ed_po = QLineEdit(); self.ed_date = QLineEdit()
        self.ed_total = QLineEdit(); self.ed_vat = QLineEdit(); self.ed_grand = QLineEdit()
        g.addWidget(QLabel("เลขที่ PO"), 0, 0); g.addWidget(self.ed_po, 0, 1)
        g.addWidget(QLabel("วันที่ (YYYY-MM-DD)"), 0, 2); g.addWidget(self.ed_date, 0, 3)
        g.addWidget(QLabel("รวมราคาสินค้า"), 1, 0); g.addWidget(self.ed_total, 1, 1)
        g.addWidget(QLabel("VAT"), 1, 2); g.addWidget(self.ed_vat, 1, 3)
        g.addWidget(QLabel("รวมทั้งสิ้น"), 1, 4); g.addWidget(self.ed_grand, 1, 5)
        for e in (self.ed_total, self.ed_vat, self.ed_grand):
            e.editingFinished.connect(self._recalc_from_fields)
        rv.addWidget(hdr)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.horizontalHeader().setSectionResizeMode(C_DESC, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(C_MATCH, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(C_TMC, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(C_STOCK, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        rv.addWidget(self.table, 1)

        rowb = QHBoxLayout()
        b_add = QPushButton("+ เพิ่มแถว")
        b_add.clicked.connect(lambda: self._append_row(POLine()))
        b_del = QPushButton("- ลบแถวที่เลือก")
        b_del.clicked.connect(self.delete_rows)
        rowb.addWidget(b_add); rowb.addWidget(b_del); rowb.addStretch()
        b_recalc = QPushButton("คำนวณยอดรวมใหม่")
        b_recalc.clicked.connect(self._recalc_totals)
        rowb.addWidget(b_recalc)
        rv.addLayout(rowb)

        self.lbl_warn = QLabel("")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setStyleSheet("color:#b36b00;")
        rv.addWidget(self.lbl_warn)

        bottom = QHBoxLayout()
        self.btn_save_all = QPushButton("🧾 รวมทุกไฟล์ในโฟลเดอร์เป็น Excel เดียว")
        self.btn_save_all.setToolTip("นำรายการสินค้าจากทุก PO ที่อ่านแล้ว มาต่อกันในชีตเดียว")
        self.btn_save_all.clicked.connect(self.save_combined)
        self.btn_save = QPushButton("💾 บันทึกไฟล์นี้เป็น Excel + เก็บสรุปรายเดือน")
        self.btn_save.clicked.connect(self.save_current)
        bottom.addStretch()
        bottom.addWidget(self.btn_save_all)
        bottom.addWidget(self.btn_save)
        rv.addLayout(bottom)

        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 900])

    # ---------------- data flow ----------------
    def refresh_customers(self):
        self.cmb_customer.blockSignals(True)
        self.cmb_customer.clear()
        self.cmb_customer.addItems(self.ctx.customers())
        self.cmb_customer.blockSignals(False)
        if self.cmb_customer.count():
            self._customer_changed(self.cmb_customer.currentText())

    def _customer_changed(self, name: str):
        if not name:
            return
        self.ctx.set_customer(name)
        # default to the customer's PO folder
        folder = Path(self.ctx.cfg["root_folder"]) / name / self.ctx.cfg["po_subfolder"]
        if folder.exists():
            self._load_folder(str(folder))
        if self.ctx.matcher is None:
            self.lbl_warn.setText("⚠ ไม่พบไฟล์ Product Details.xlsx ของลูกค้านี้ — tmc_code จะต้องกรอกเอง")
        else:
            self.lbl_warn.setText("")

    def choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "เลือกโฟลเดอร์ที่เก็บไฟล์ PO")
        if d:
            self._load_folder(d)

    def choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "เลือกไฟล์ PDF", "", "PDF (*.pdf)")
        if files:
            self.lst.clear()
            self.docs.clear()
            for f in files:
                self._add_pdf_item(f)

    def import_po(self):
        cust = self.cmb_customer.currentText()
        if not cust:
            QMessageBox.warning(self, "นำเข้า PO", "กรุณาเลือกลูกค้าก่อน")
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "เลือกไฟล์ PO (PDF) ที่จะนำเข้าโฟลเดอร์ลูกค้า", "", "PDF (*.pdf)")
        if not files:
            return
        try:
            copied, skipped = customers.import_po_files(self.ctx.cfg, cust, files)
        except customers.RegisterError as e:
            QMessageBox.warning(self, "นำเข้า PO", str(e))
            return
        folder = Path(self.ctx.cfg["root_folder"]) / cust / self.ctx.cfg["po_subfolder"]
        self._load_folder(str(folder))
        msg = f"นำเข้า {len(copied)} ไฟล์เข้าโฟลเดอร์ '{cust}' แล้ว"
        if skipped:
            msg += f"\nข้าม {len(skipped)} ไฟล์ (มีอยู่แล้ว): {', '.join(skipped[:5])}"
        QMessageBox.information(self, "นำเข้า PO", msg)

    def _load_folder(self, folder: str):
        self.lst.clear()
        self.docs.clear()
        for f in sorted(Path(folder).glob("*.pdf")):
            self._add_pdf_item(str(f))

    def _add_pdf_item(self, path: str):
        it = QListWidgetItem("◻ " + Path(path).name)
        it.setData(Qt.UserRole, path)
        self.lst.addItem(it)

    def _pdf_selected(self, cur, _prev):
        # capture edits on the previously shown doc first
        self._capture_table()
        if not cur:
            return
        path = cur.data(Qt.UserRole)
        self.current_path = path
        if path in self.docs:
            self._load_doc(self.docs[path])
        else:
            self._clear_table()


    def delete_selected_po_files(self):

        """Delete selected PO PDF files from disk and remove them from the list."""

        if self.worker is not None and hasattr(self.worker, "isRunning") and self.worker.isRunning():

            QMessageBox.warning(self, "ลบไฟล์ PO", "ระบบกำลังอ่านเอกสาร OCR อยู่ กรุณารอให้เสร็จก่อน")

            return


        selected = self.lst.selectedItems()

        if not selected and self.lst.currentItem():

            selected = [self.lst.currentItem()]


        if not selected:

            QMessageBox.information(self, "ลบไฟล์ PO", "กรุณาเลือกไฟล์ PO ที่ต้องการลบก่อน")

            return


        items_to_delete = []

        seen = set()

        for item in selected:

            raw_path = item.data(Qt.UserRole)

            if not raw_path:

                continue

            path = Path(str(raw_path))

            key = str(path.resolve()) if path.exists() else str(path)

            if key not in seen:

                seen.add(key)

                items_to_delete.append((item, path, str(raw_path)))


        if not items_to_delete:

            QMessageBox.information(self, "ลบไฟล์ PO", "ไม่พบไฟล์ PO ที่เลือก")

            return


        names = [path.name for _item, path, _raw in items_to_delete]

        preview = "\n".join(names[:10])

        if len(names) > 10:

            preview += f"\n... อีก {len(names) - 10} ไฟล์"


        answer = QMessageBox.question(

            self,

            "ยืนยันลบไฟล์ PO",

            f"ต้องการเอาไฟล์ PO ออกจากรายการ {len(items_to_delete)} ไฟล์ออกจากโฟลเดอร์ลูกค้าหรือไม่?\n\n"

            f"{preview}\n\n"

            "หมายเหตุ: ระบบจะไม่ลบไฟล์จริง แต่จะย้ายไปโฟลเดอร์ _REMOVED_FROM_OCR และไม่ลบไฟล์ Excel ที่เคย Export แล้ว",

        )

        if answer != QMessageBox.Yes:

            return


        deleted_paths = set()

        failed = []

        for _item, path, raw_path in items_to_delete:

            try:

                if path.exists():

                    path.unlink()

                deleted_paths.add(str(path.resolve()) if path.exists() else str(path))

                deleted_paths.add(raw_path)

                self.docs.pop(raw_path, None)

                self.docs.pop(str(path), None)

            except Exception as exc:

                failed.append(f"{path.name}: {exc}")


        # Remove successfully deleted items from the QListWidget.

        for row in range(self.lst.count() - 1, -1, -1):

            item = self.lst.item(row)

            raw_path = item.data(Qt.UserRole)

            if not raw_path:

                continue

            path = Path(str(raw_path))

            keys = {str(raw_path), str(path)}

            try:

                keys.add(str(path.resolve()))

            except Exception:

                pass

            if keys & deleted_paths:

                self.lst.takeItem(row)


        if self.current_path:

            cur_path = Path(str(self.current_path))

            cur_keys = {str(self.current_path), str(cur_path)}

            try:

                cur_keys.add(str(cur_path.resolve()))

            except Exception:

                pass

            if cur_keys & deleted_paths:

                self.current_path = ""

                self._clear_table()

                if self.lst.count():

                    self.lst.setCurrentRow(0)


        msg = f"ลบไฟล์ PO สำเร็จ {len(items_to_delete) - len(failed)} ไฟล์"

        if failed:

            msg += "\n\nลบไม่สำเร็จ:\n" + "\n".join(failed[:10])

            QMessageBox.warning(self, "ลบไฟล์ PO", msg)

        else:

            QMessageBox.information(self, "ลบไฟล์ PO", msg)


    # ---------------- OCR ----------------
    def run_ocr(self):
        cust = self.cmb_customer.currentText()
        if not cust:
            QMessageBox.warning(self, "เลือกลูกค้า", "กรุณาเลือกลูกค้าก่อน")
            return
        paths = [self.lst.item(i).data(Qt.UserRole) for i in range(self.lst.count())]
        if not paths:
            QMessageBox.information(self, "ไม่มีไฟล์", "ยังไม่ได้เลือกไฟล์ PO")
            return
        self.btn_ocr.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(paths))
        self.worker = OcrWorker(self.ctx, cust, paths)
        self.worker.progress.connect(self._on_progress)
        self.worker.one_done.connect(self._on_doc)
        self.worker.error.connect(lambda m: QMessageBox.critical(self, "ผิดพลาด", m))
        self.worker.finished_all.connect(self._on_finish)
        self.worker.start()

    def _on_progress(self, done, total, path):
        self.progress.setValue(done - 1)
        self.setWindowTitle_safe(f"กำลังอ่าน {done}/{total}: {Path(path).name}")

    def setWindowTitle_safe(self, text):
        w = self.window()
        if w:
            w.statusBar().showMessage(text)

    def _on_doc(self, doc: PODocument):
        self.docs[doc.source_pdf] = doc
        # update list label with item count
        for i in range(self.lst.count()):
            it = self.lst.item(i)
            if it.data(Qt.UserRole) == doc.source_pdf:
                mark = "✅" if doc.lines and not doc.warnings else "⚠️"
                it.setText(f"{mark} {Path(doc.source_pdf).name}  ({doc.item_count} รายการ)")
        if doc.source_pdf == self.current_path or not self.current_path:
            self.current_path = doc.source_pdf
            self._load_doc(doc)

    def _on_finish(self):
        self.progress.setVisible(False)
        self.btn_ocr.setEnabled(True)
        self.setWindowTitle_safe("อ่านเอกสารเสร็จแล้ว")
        # auto-select first processed
        if not self.current_path and self.lst.count():
            self.lst.setCurrentRow(0)

    # ---------------- table <-> doc ----------------
    def _clear_table(self):
        self.table.setRowCount(0)
        for e in (self.ed_po, self.ed_date, self.ed_total, self.ed_vat, self.ed_grand):
            e.clear()
        self.lbl_warn.setText("")

    def _load_doc(self, doc: PODocument):
        self._clear_table()
        self.ed_po.setText(doc.po_no)
        self.ed_date.setText(doc.po_date)
        self.ed_total.setText(f"{doc.total:.2f}")
        self.ed_vat.setText(f"{doc.vat:.2f}")
        self.ed_grand.setText(f"{doc.grand_total:.2f}")
        for line in doc.lines:
            self._append_row(line)
        if doc.warnings:
            self.lbl_warn.setText("⚠ " + "  |  ".join(doc.warnings))
        else:
            self.lbl_warn.setText("✔ อ่านข้อมูลสำเร็จ โปรดตรวจทานก่อนบันทึก")

    def _append_row(self, line: POLine):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, C_ITEM, QTableWidgetItem(line.item_no))
        self.table.setItem(r, C_CODE, QTableWidgetItem(line.product_code_raw))
        self.table.setItem(r, C_DESC, QTableWidgetItem(line.description_raw))

        tmc = TmcCombo(self.ctx.tmc_items(), line.tmc_code)
        self.table.setCellWidget(r, C_TMC, tmc)

        mn = QTableWidgetItem(line.matched_name)
        mn.setFlags(mn.flags() & ~Qt.ItemIsEditable)
        mn.setForeground(QBrush(QColor("#0a58ca")))
        self.table.setItem(r, C_MATCH, mn)
        # when the user picks a different tmc_code, show that product's name here
        tmc.currentTextChanged.connect(self._on_tmc_changed)

        stock = SearchCombo(self.ctx.stock_group_codes, line.stock_group_code)
        self.table.setCellWidget(r, C_STOCK, stock)

        self.table.setItem(r, C_QTY, QTableWidgetItem(self._fmt(line.qty)))
        self.table.setItem(r, C_PRICE, QTableWidgetItem(self._fmt(line.price)))

        st = QTableWidgetItem(f"{line.match_status} {line.match_score:.0f}")
        st.setFlags(st.flags() & ~Qt.ItemIsEditable)
        st.setForeground(QBrush(self._status_color(line.match_status)))
        self.table.setItem(r, C_STATUS, st)

    def _on_tmc_changed(self, _text):
        """User picked/typed a tmc_code -> refresh the matched product name shown."""
        w = self.sender()
        if w is None:
            return
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, C_TMC) is w:
                code = w.value() if hasattr(w, "value") else _text
                name = self.ctx.tmc_to_name().get(code, "")
                it = self.table.item(r, C_MATCH)
                if it is None:
                    it = QTableWidgetItem()
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                    self.table.setItem(r, C_MATCH, it)
                it.setText(name)
                return

    @staticmethod
    def _fmt(v: float) -> str:
        return ("%g" % v) if v else "0"

    @staticmethod
    def _status_color(status: str) -> QColor:
        return {
            "matched": QColor("#1a7f37"),
            "fuzzy": QColor("#b36b00"),
            "manual": QColor("#0a58ca"),
        }.get(status, QColor("#b02a37"))

    def _capture_table(self):
        """Read the visible table back into the current document."""
        if not self.current_path or self.current_path not in self.docs:
            return
        doc = self.docs[self.current_path]
        doc.po_no = self.ed_po.text().strip()
        doc.po_date = self.ed_date.text().strip()
        doc.total = self._num(self.ed_total.text())
        doc.vat = self._num(self.ed_vat.text())
        doc.grand_total = self._num(self.ed_grand.text())
        lines = []
        for r in range(self.table.rowCount()):
            tmc_w = self.table.cellWidget(r, C_TMC)
            stock_w = self.table.cellWidget(r, C_STOCK)
            line = POLine(
                item_no=self._cell(r, C_ITEM),
                product_code_raw=self._cell(r, C_CODE),
                description_raw=self._cell(r, C_DESC),
                tmc_code=tmc_w.value() if tmc_w else "",
                matched_name=self._cell(r, C_MATCH),
                stock_group_code=stock_w.value() if stock_w else "",
                qty=self._num(self._cell(r, C_QTY)),
                price=self._num(self._cell(r, C_PRICE)),
            )
            lines.append(line)
        doc.lines = lines

    def _cell(self, r, c) -> str:
        it = self.table.item(r, c)
        return it.text().strip() if it else ""

    @staticmethod
    def _num(s: str) -> float:
        try:
            return float(str(s).replace(",", "").strip() or 0)
        except ValueError:
            return 0.0

    # ---------------- actions ----------------
    def delete_rows(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
        self._recalc_totals()

    def _recalc_totals(self):
        total = 0.0
        for r in range(self.table.rowCount()):
            total += self._num(self._cell(r, C_QTY)) * self._num(self._cell(r, C_PRICE))
        vat = round(total * 0.07, 2)
        self.ed_total.setText(f"{total:.2f}")
        self.ed_vat.setText(f"{vat:.2f}")
        self.ed_grand.setText(f"{total + vat:.2f}")

    def _recalc_from_fields(self):
        try:
            t = self._num(self.ed_total.text())
            v = self._num(self.ed_vat.text())
            self.ed_grand.setText(f"{t + v:.2f}")
        except Exception:
            pass

    def teach_layout(self):
        cust = self.cmb_customer.currentText()
        if not cust:
            QMessageBox.warning(self, "สอนตำแหน่ง", "กรุณาเลือกลูกค้าก่อน")
            return
        # use the currently selected PDF, else the first in the list
        path = self.current_path
        if not path and self.lst.count():
            path = self.lst.item(0).data(Qt.UserRole)
        if not path:
            QMessageBox.information(self, "สอนตำแหน่ง", "ยังไม่มีไฟล์ PO ให้เปิด")
            return
        from .layout_teacher import LayoutTeacherDialog
        dlg = LayoutTeacherDialog(self.ctx, cust, path, self)
        if dlg.exec():
            r = QMessageBox.question(
                self, "สอนตำแหน่งแล้ว",
                "บันทึกตำแหน่งเรียบร้อย ต้องการอ่านเอกสาร (OCR) ใหม่ทันทีหรือไม่?")
            if r == QMessageBox.Yes:
                self.run_ocr()

    def relearn(self):
        if not self.current_path or self.current_path not in self.docs:
            QMessageBox.information(self, "เรียนรู้ตำแหน่ง", "โปรดอ่านเอกสาร (OCR) ก่อน")
            return
        doc = self.docs[self.current_path]
        if pipeline.relearn_layout(doc):
            QMessageBox.information(self, "เรียนรู้ตำแหน่ง",
                                    f"บันทึกตำแหน่งคอลัมน์เป็นแม่แบบของลูกค้า '{doc.customer}' แล้ว")
        else:
            QMessageBox.warning(self, "เรียนรู้ตำแหน่ง", "ไม่มีข้อมูลตำแหน่งให้บันทึก")

    def save_current(self):
        self._capture_table()
        if not self.current_path or self.current_path not in self.docs:
            QMessageBox.information(self, "บันทึก", "ยังไม่มีเอกสารให้บันทึก")
            return
        doc = self.docs[self.current_path]
        if not doc.po_no:
            QMessageBox.warning(self, "บันทึก", "กรุณาระบุเลขที่ PO")
            return
        missing = [i + 1 for i, l in enumerate(doc.lines) if not l.tmc_code]
        if missing:
            r = QMessageBox.question(
                self, "ยังไม่มี tmc_code",
                f"มี {len(missing)} แถวที่ยังไม่มี tmc_code (แถว {missing[:8]}...)\nต้องการบันทึกต่อหรือไม่?")
            if r != QMessageBox.Yes:
                return
        default = exporter.default_output_path(
            self.ctx.cfg["root_folder"], doc.customer,
            self.ctx.cfg["invoice_subfolder"], doc.po_no)
        out, _ = QFileDialog.getSaveFileName(
            self, "บันทึกไฟล์ Excel (เลือกที่เก็บได้)", default, "Excel (*.xlsx)")
        if not out:
            return
        if not out.lower().endswith(".xlsx"):
            out += ".xlsx"
        exporter.export_invoice(doc, out)
        self.ctx.store.save_invoice(doc, out)
        QMessageBox.information(self, "บันทึกสำเร็จ",
                               f"บันทึก Excel แล้ว:\n{out}\n\nและเก็บสรุปรายเดือนเรียบร้อย")
        if self.on_saved:
            self.on_saved()

    def save_combined(self):
        """รวมทุกไฟล์ PO ที่อ่านแล้วในโฟลเดอร์ ให้เป็น Excel ไฟล์เดียว (ชีตเดียว)."""
        self._capture_table()
        # เรียงตามลำดับไฟล์ในรายการ และเอาเฉพาะที่ผ่าน OCR แล้ว
        ordered = []
        for i in range(self.lst.count()):
            p = self.lst.item(i).data(Qt.UserRole)
            if p in self.docs:
                ordered.append(self.docs[p])
        if not ordered:
            QMessageBox.information(self, "รวมไฟล์", "ยังไม่ได้อ่านเอกสาร (OCR) กรุณากด \"อ่านเอกสาร\" ก่อน")
            return
        not_read = self.lst.count() - len(ordered)
        if not_read > 0:
            r = QMessageBox.question(
                self, "รวมไฟล์",
                f"มี {not_read} ไฟล์ที่ยังไม่ได้อ่าน OCR\n"
                f"จะรวมเฉพาะ {len(ordered)} ไฟล์ที่อ่านแล้วหรือไม่?")
            if r != QMessageBox.Yes:
                return
        cust = self.cmb_customer.currentText() or ordered[0].customer
        default = exporter.default_combined_path(
            self.ctx.cfg["root_folder"], cust, self.ctx.cfg["invoice_subfolder"])
        out, _ = QFileDialog.getSaveFileName(
            self, "บันทึกไฟล์ Excel รวม (เลือกที่เก็บได้)", default, "Excel (*.xlsx)")
        if not out:
            return
        if not out.lower().endswith(".xlsx"):
            out += ".xlsx"
        exporter.export_combined(ordered, out)
        # เก็บสรุปรายเดือนของแต่ละใบ (ชี้ไปที่ไฟล์รวม)
        saved = 0
        for doc in ordered:
            if doc.po_no:
                self.ctx.store.save_invoice(doc, out)
                saved += 1
        total_items = sum(d.item_count for d in ordered)
        QMessageBox.information(
            self, "บันทึกไฟล์รวมสำเร็จ",
            f"รวม {len(ordered)} ไฟล์ ({total_items} รายการ) เป็นไฟล์เดียว:\n{out}\n\n"
            f"และเก็บสรุปรายเดือน {saved} ใบเรียบร้อย")
        if self.on_saved:
            self.on_saved()
# === PHASE1 UX PATCH: drag-drop-auto-validate ===
# This block is appended by apply_phase1_patch.py. It keeps the original UI
# mostly intact, then adds Drag & Drop, auto OCR, and export validation.
try:
    from .. import validator as _phase1_validator
    from ..models import POLine as _Phase1POLine

    def _phase1_pdf_paths_from_event(event):
        out = []
        try:
            urls = event.mimeData().urls()
        except Exception:
            return out
        for url in urls:
            path = url.toLocalFile()
            if path and Path(path).is_file() and Path(path).suffix.lower() == ".pdf":
                out.append(path)
        return out

    def _phase1_dragEnterEvent(self, event):
        if _phase1_pdf_paths_from_event(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def _phase1_dropEvent(self, event):
        paths = _phase1_pdf_paths_from_event(event)
        if not paths:
            event.ignore()
            return
        cust = self.cmb_customer.currentText()
        if cust:
            try:
                copied, skipped = customers.import_po_files(self.ctx.cfg, cust, paths)
            except Exception as e:
                QMessageBox.warning(self, "ลากไฟล์ PO", str(e))
                return
            folder = Path(self.ctx.cfg["root_folder"]) / cust / self.ctx.cfg["po_subfolder"]
            self._load_folder(str(folder))
            msg = f"นำเข้า {len(copied)} ไฟล์แล้ว"
            if skipped:
                msg += f"\nข้าม {len(skipped)} ไฟล์: {', '.join(skipped[:5])}"
            self.setWindowTitle_safe(msg)
            if copied:
                self.run_ocr()
        else:
            self.lst.clear()
            self.docs.clear()
            for path in paths:
                self._add_pdf_item(path)
            self.run_ocr()
        event.acceptProposedAction()

    def _phase1_load_folder(self, folder: str):
        self.lst.clear()
        self.docs.clear()
        base = Path(folder)
        if not base.exists():
            return
        for f in sorted([p for p in base.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]):
            self._add_pdf_item(str(f))

    def _phase1_import_po(self):
        cust = self.cmb_customer.currentText()
        if not cust:
            QMessageBox.warning(self, "นำเข้า PO", "กรุณาเลือกลูกค้าก่อน")
            return
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "เลือกไฟล์ PO (PDF) ที่จะนำเข้าโฟลเดอร์ลูกค้า",
            "",
            "PDF (*.pdf *.PDF)",
        )
        if not files:
            return
        try:
            copied, skipped = customers.import_po_files(self.ctx.cfg, cust, files)
        except customers.RegisterError as e:
            QMessageBox.warning(self, "นำเข้า PO", str(e))
            return
        folder = Path(self.ctx.cfg["root_folder"]) / cust / self.ctx.cfg["po_subfolder"]
        self._load_folder(str(folder))
        msg = f"นำเข้า {len(copied)} ไฟล์เข้าโฟลเดอร์ '{cust}' แล้ว"
        if skipped:
            msg += f"\nข้าม {len(skipped)} ไฟล์: {', '.join(skipped[:5])}"
        QMessageBox.information(self, "นำเข้า PO", msg)
        if copied:
            self.run_ocr()

    def _phase1_choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "เลือกไฟล์ PDF", "", "PDF (*.pdf *.PDF)")
        if files:
            self.lst.clear()
            self.docs.clear()
            for f in files:
                if Path(f).suffix.lower() == ".pdf":
                    self._add_pdf_item(f)
            self.run_ocr()

    def _phase1_doc_threshold(self) -> float:
        try:
            return float(self.ctx.cfg.get("fuzzy_strong", 90))
        except Exception:
            return 90.0

    def _phase1_validate_doc(self, doc):
        return _phase1_validator.validate_document(
            doc,
            fuzzy_review_threshold=_phase1_doc_threshold(self),
            require_stock_group=False,
        )

    def _phase1_show_validation(self, doc):
        issues = _phase1_validate_doc(self, doc)
        critical, review = _phase1_validator.split_issues(issues)
        if critical:
            self.lbl_warn.setStyleSheet("color:#b02a37;")
            self.lbl_warn.setText("❌ ต้องแก้ก่อน Export\n" + _phase1_validator.summarize_issues(critical))
        elif review:
            self.lbl_warn.setStyleSheet("color:#b36b00;")
            self.lbl_warn.setText("⚠ ควรตรวจทานก่อน Export\n" + _phase1_validator.summarize_issues(review))
        elif getattr(doc, "warnings", None):
            self.lbl_warn.setStyleSheet("color:#b36b00;")
            self.lbl_warn.setText("⚠ " + " | ".join(doc.warnings))
        else:
            self.lbl_warn.setStyleSheet("color:#1a7f37;")
            self.lbl_warn.setText("✅ ผ่านการตรวจสอบ พร้อม Export")
        return critical, review

    _phase1_original_init = ProcessTab.__init__
    def _phase1_init(self, *args, **kwargs):
        _phase1_original_init(self, *args, **kwargs)
        self.setAcceptDrops(True)
        try:
            self.lst.setAcceptDrops(False)
            self.setToolTip("สามารถลากไฟล์ PDF มาวางในหน้านี้ได้ ระบบจะนำเข้าและ OCR ให้อัตโนมัติ")
        except Exception:
            pass

    _phase1_original_append_row = ProcessTab._append_row
    def _phase1_append_row(self, line):
        _phase1_original_append_row(self, line)
        r = self.table.rowCount() - 1
        issues = _phase1_validator.validate_line(
            line,
            r + 1,
            fuzzy_review_threshold=_phase1_doc_threshold(self),
            require_stock_group=False,
        )
        critical, review = _phase1_validator.split_issues(issues)
        st = self.table.item(r, C_STATUS)
        if st is not None:
            if critical:
                st.setText("ต้องแก้: " + ", ".join(i.message for i in critical[:2]))
                st.setForeground(QBrush(QColor("#b02a37")))
            elif review:
                st.setText("ตรวจทาน: " + ", ".join(i.message for i in review[:2]))
                st.setForeground(QBrush(QColor("#b36b00")))
            else:
                st.setText("ผ่าน")
                st.setForeground(QBrush(QColor("#1a7f37")))

    _phase1_original_on_doc = ProcessTab._on_doc
    def _phase1_on_doc(self, doc):
        self.docs[doc.source_pdf] = doc
        issues = _phase1_validate_doc(self, doc)
        critical, review = _phase1_validator.split_issues(issues)
        for i in range(self.lst.count()):
            it = self.lst.item(i)
            if it.data(Qt.UserRole) == doc.source_pdf:
                if critical:
                    mark = "❌"
                    note = f"ต้องแก้ {len(critical)} จุด"
                elif review or doc.warnings:
                    mark = "⚠️"
                    note = f"ตรวจทาน {len(review) + len(doc.warnings)} จุด"
                else:
                    mark = "✅"
                    note = "พร้อม Export"
                it.setText(f"{mark} {Path(doc.source_pdf).name} ({doc.item_count} รายการ / {note})")
        if doc.source_pdf == self.current_path or not self.current_path:
            self.current_path = doc.source_pdf
            self._load_doc(doc)

    _phase1_original_load_doc = ProcessTab._load_doc
    def _phase1_load_doc(self, doc):
        _phase1_original_load_doc(self, doc)
        _phase1_show_validation(self, doc)

    def _phase1_capture_table(self):
        """Read table back into current doc while preserving match status/score/amount."""
        if not self.current_path or self.current_path not in self.docs:
            return
        doc = self.docs[self.current_path]
        doc.po_no = self.ed_po.text().strip()
        doc.po_date = self.ed_date.text().strip()
        doc.total = self._num(self.ed_total.text())
        doc.vat = self._num(self.ed_vat.text())
        doc.grand_total = self._num(self.ed_grand.text())
        old_lines = list(getattr(doc, "lines", []) or [])
        lines = []
        for r in range(self.table.rowCount()):
            old = old_lines[r] if r < len(old_lines) else _Phase1POLine()
            tmc_w = self.table.cellWidget(r, C_TMC)
            stock_w = self.table.cellWidget(r, C_STOCK)
            new_tmc = tmc_w.value() if tmc_w else ""
            line = _Phase1POLine(
                item_no=self._cell(r, C_ITEM),
                product_code_raw=self._cell(r, C_CODE),
                description_raw=self._cell(r, C_DESC),
                tmc_code=new_tmc,
                matched_name=self._cell(r, C_MATCH),
                stock_group_code=stock_w.value() if stock_w else "",
                qty=self._num(self._cell(r, C_QTY)),
                price=self._num(self._cell(r, C_PRICE)),
                amount=getattr(old, "amount", 0.0),
                match_score=getattr(old, "match_score", 0.0),
                match_status=getattr(old, "match_status", "no_match"),
            )
            if new_tmc and new_tmc != getattr(old, "tmc_code", ""):
                line.match_status = "manual"
                line.match_score = 100.0
            lines.append(line)
        doc.lines = lines
        _phase1_show_validation(self, doc)

    def _phase1_confirm_export(self, docs):
        all_critical = []
        all_review = []
        for doc in docs:
            issues = _phase1_validate_doc(self, doc)
            critical, review = _phase1_validator.split_issues(issues)
            all_critical.extend((doc, issue) for issue in critical)
            all_review.extend((doc, issue) for issue in review)
        if all_critical:
            lines = []
            for doc, issue in all_critical[:10]:
                lines.append(f"{Path(doc.source_pdf).name}: {issue.as_text()}")
            if len(all_critical) > 10:
                lines.append(f"...และอีก {len(all_critical) - 10} จุด")
            QMessageBox.warning(self, "ยัง Export ไม่ได้", "กรุณาแก้ข้อมูลสำคัญก่อน Export:\n\n" + "\n".join(lines))
            return False
        if all_review:
            lines = []
            for doc, issue in all_review[:10]:
                lines.append(f"{Path(doc.source_pdf).name}: {issue.as_text()}")
            if len(all_review) > 10:
                lines.append(f"...และอีก {len(all_review) - 10} จุด")
            r = QMessageBox.question(
                self,
                "มีรายการที่ควรตรวจทาน",
                "พบข้อมูลที่ควรตรวจทานก่อน Export:\n\n" + "\n".join(lines) + "\n\nต้องการ Export ต่อหรือไม่?",
            )
            return r == QMessageBox.Yes
        return True

    _phase1_original_save_current = ProcessTab.save_current
    def _phase1_save_current(self):
        self._capture_table()
        if not self.current_path or self.current_path not in self.docs:
            QMessageBox.information(self, "บันทึก", "ยังไม่มีเอกสารให้บันทึก")
            return
        doc = self.docs[self.current_path]
        if not _phase1_confirm_export(self, [doc]):
            return
        _phase1_original_save_current(self)

    _phase1_original_save_combined = ProcessTab.save_combined
    def _phase1_save_combined(self):
        self._capture_table()
        ordered = []
        for i in range(self.lst.count()):
            p = self.lst.item(i).data(Qt.UserRole)
            if p in self.docs:
                ordered.append(self.docs[p])
        if ordered and not _phase1_confirm_export(self, ordered):
            return
        _phase1_original_save_combined(self)

    ProcessTab.__init__ = _phase1_init
    ProcessTab.dragEnterEvent = _phase1_dragEnterEvent
    ProcessTab.dropEvent = _phase1_dropEvent
    ProcessTab._load_folder = _phase1_load_folder
    ProcessTab.import_po = _phase1_import_po
    ProcessTab.choose_files = _phase1_choose_files
    ProcessTab._append_row = _phase1_append_row
    ProcessTab._on_doc = _phase1_on_doc
    ProcessTab._load_doc = _phase1_load_doc
    ProcessTab._capture_table = _phase1_capture_table
    ProcessTab.save_current = _phase1_save_current
    ProcessTab.save_combined = _phase1_save_combined
except Exception as _phase1_patch_error:
    # Keep the app startable even if this patch block has an unexpected issue.
    print("PHASE1 UX PATCH disabled:", _phase1_patch_error)
# === END PHASE1 UX PATCH ===

# === PHASE2 MAPPING MEMORY UI PATCH ===
try:
    from .. import mapping_memory as _phase2_mapping_memory

    _phase2_original_status_color = ProcessTab._status_color

    def _phase2_status_color(status: str):
        status = str(status or "").lower().strip()
        if status == "remembered":
            return QColor("#6f42c1")
        if status == "manual":
            return QColor("#0a58ca")
        return _phase2_original_status_color(status)

    ProcessTab._status_color = staticmethod(_phase2_status_color)

    _phase2_original_on_tmc_changed = ProcessTab._on_tmc_changed

    def _phase2_on_tmc_changed(self, text):
        _phase2_original_on_tmc_changed(self, text)
        sender = self.sender()
        if sender is None:
            return
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, C_TMC) is sender:
                st = self.table.item(r, C_STATUS)
                if st is None:
                    st = QTableWidgetItem()
                    st.setFlags(st.flags() & ~Qt.ItemIsEditable)
                    self.table.setItem(r, C_STATUS, st)
                # If the user changes/selects a tmc_code, treat it as a manual correction.
                value = sender.value() if hasattr(sender, "value") else str(text or "")
                if str(value or "").strip():
                    st.setText("manual 100")
                    st.setForeground(QBrush(QColor("#0a58ca")))
                return

    ProcessTab._on_tmc_changed = _phase2_on_tmc_changed

    _phase2_original_capture_table = ProcessTab._capture_table

    def _phase2_capture_table(self):
        _phase2_original_capture_table(self)
        if not self.current_path or self.current_path not in self.docs:
            return
        doc = self.docs[self.current_path]
        # Restore match_status/match_score from the visible status column because
        # the original capture_table recreates POLine objects without those fields.
        for r, line in enumerate(doc.lines):
            st_text = self._cell(r, C_STATUS) if r < self.table.rowCount() else ""
            parts = st_text.split()
            if parts:
                line.match_status = parts[0].strip()
            if len(parts) >= 2:
                try:
                    line.match_score = float(parts[1])
                except Exception:
                    pass
        # Save local memory so the next OCR run can fill repeated corrections automatically.
        try:
            _phase2_mapping_memory.remember_document(doc.customer or self.cmb_customer.currentText(), doc)
        except Exception:
            pass

    ProcessTab._capture_table = _phase2_capture_table

except Exception:
    pass

# === PHASE3 REVIEW WORKFLOW PATCH ===
try:
    from PySide6.QtWidgets import QCheckBox as _Phase3QCheckBox
    from PySide6.QtWidgets import QHBoxLayout as _Phase3QHBoxLayout
    from PySide6.QtWidgets import QPushButton as _Phase3QPushButton
    from PySide6.QtWidgets import QLabel as _Phase3QLabel
    from .. import validator as _phase3_validator

    def _phase3_threshold(self) -> float:
        try:
            return float(self.ctx.cfg.get("fuzzy_strong", 90))
        except Exception:
            return 90.0

    def _phase3_current_doc(self):
        if self.current_path and self.current_path in self.docs:
            return self.docs[self.current_path]
        return None

    def _phase3_validate_doc(self, doc):
        return _phase3_validator.validate_document(
            doc,
            fuzzy_review_threshold=_phase3_threshold(self),
            require_stock_group=False,
        )

    def _phase3_split_doc(self, doc):
        issues = _phase3_validate_doc(self, doc)
        return _phase3_validator.split_issues(issues)

    def _phase3_issues_by_row(self, doc):
        issues = _phase3_validate_doc(self, doc)
        out = {}
        for issue in issues:
            if issue.row > 0:
                out.setdefault(issue.row, []).append(issue)
        return out

    def _phase3_update_file_status_summary(self):
        total = self.lst.count()
        read = len(getattr(self, "docs", {}) or {})
        critical_files = 0
        review_files = 0
        for doc in (getattr(self, "docs", {}) or {}).values():
            critical, review = _phase3_split_doc(self, doc)
            if critical:
                critical_files += 1
            elif review or getattr(doc, "warnings", None):
                review_files += 1
        if hasattr(self, "lbl_phase3_file_status"):
            self.lbl_phase3_file_status.setText(
                f"สถานะไฟล์: ทั้งหมด {total} | OCR แล้ว {read} | ต้องแก้ {critical_files} | ควรตรวจ {review_files}"
            )

    def _phase3_set_row_bg(self, row: int, color):
        brush = QBrush(color) if color is not None else QBrush()
        for c in range(self.table.columnCount()):
            it = self.table.item(row, c)
            if it is not None:
                it.setBackground(brush)

    def _phase3_apply_row_review_state(self, show_message: bool = False):
        doc = _phase3_current_doc(self)
        if doc is None:
            if hasattr(self, "lbl_phase3_review_summary"):
                self.lbl_phase3_review_summary.setText("ยังไม่มีเอกสารที่ OCR แล้วให้ตรวจสอบ")
            return

        try:
            self._capture_table()
            doc = _phase3_current_doc(self) or doc
        except Exception:
            pass

        row_map = _phase3_issues_by_row(self, doc)
        doc_issues = _phase3_validate_doc(self, doc)
        critical, review = _phase3_validator.split_issues(doc_issues)

        crit_rows = set()
        review_rows = set()
        for row_no, issues in row_map.items():
            if any(i.level == "critical" for i in issues):
                crit_rows.add(row_no - 1)
            elif issues:
                review_rows.add(row_no - 1)

        review_only = bool(getattr(self, "chk_phase3_review_only", None) and self.chk_phase3_review_only.isChecked())

        shown = 0
        for r in range(self.table.rowCount()):
            if r in crit_rows:
                _phase3_set_row_bg(self, r, QColor("#ffe1e1"))
                hidden = False
            elif r in review_rows:
                _phase3_set_row_bg(self, r, QColor("#fff3cd"))
                hidden = False
            else:
                _phase3_set_row_bg(self, r, None)
                hidden = review_only
            try:
                self.table.setRowHidden(r, hidden)
            except Exception:
                pass
            if not hidden:
                shown += 1

        summary = f"ตรวจสอบไฟล์นี้: ต้องแก้ {len(critical)} จุด | ควรตรวจ {len(review)} จุด | แถวที่แสดง {shown}/{self.table.rowCount()}"
        if hasattr(self, "lbl_phase3_review_summary"):
            if critical:
                self.lbl_phase3_review_summary.setStyleSheet("color:#b02a37;font-weight:600;")
            elif review:
                self.lbl_phase3_review_summary.setStyleSheet("color:#b36b00;font-weight:600;")
            else:
                self.lbl_phase3_review_summary.setStyleSheet("color:#1a7f37;font-weight:600;")
            self.lbl_phase3_review_summary.setText(summary)

        _phase3_update_file_status_summary(self)

        if show_message:
            if critical:
                QMessageBox.warning(
                    self,
                    "ผลตรวจสอบ",
                    "ยังมีรายการที่ต้องแก้ก่อน Export:\n" + _phase3_validator.summarize_issues(critical, limit=12),
                )
            elif review:
                QMessageBox.information(
                    self,
                    "ผลตรวจสอบ",
                    "มีรายการที่ควรตรวจทาน:\n\n" + _phase3_validator.summarize_issues(review, limit=12),
                )
            else:
                QMessageBox.information(self, "ผลตรวจสอบ", "ไฟล์นี้ผ่านการตรวจสอบ พร้อม Export")

    def _phase3_focus_next_issue(self):
        doc = _phase3_current_doc(self)
        if doc is not None:
            try:
                self._capture_table()
                doc = _phase3_current_doc(self) or doc
            except Exception:
                pass
            rows = sorted((row_no - 1) for row_no in _phase3_issues_by_row(self, doc).keys() if row_no > 0)
            if rows:
                cur = self.table.currentRow()
                target = None
                for r in rows:
                    if r > cur:
                        target = r
                        break
                if target is None:
                    target = rows[0]
                try:
                    self.table.setRowHidden(target, False)
                    self.table.selectRow(target)
                    item = self.table.item(target, 0)
                    if item is not None:
                        self.table.scrollToItem(item)
                except Exception:
                    pass
                return

        count = self.lst.count()
        if count <= 0:
            return
        start = self.lst.currentRow()
        if start < 0:
            start = 0
        for offset in range(1, count + 1):
            idx = (start + offset) % count
            path = self.lst.item(idx).data(Qt.UserRole)
            doc2 = self.docs.get(path) if hasattr(self, "docs") else None
            if doc2 is None:
                continue
            c, r = _phase3_split_doc(self, doc2)
            if c or r or getattr(doc2, "warnings", None):
                self.lst.setCurrentRow(idx)
                return
        QMessageBox.information(self, "ถัดไป", "ไม่พบรายการที่ต้องแก้/ตรวจทานในไฟล์ที่ OCR แล้ว")

    _phase3_original_build = ProcessTab._build

    def _phase3_build(self):
        _phase3_original_build(self)

        try:
            self.lbl_phase3_file_status = _Phase3QLabel("สถานะไฟล์: -")
            self.lbl_phase3_file_status.setWordWrap(True)
            self.lbl_phase3_file_status.setStyleSheet("color:#57606a;padding:4px 0;")
            left_layout = self.lst.parentWidget().layout()
            idx = left_layout.indexOf(self.btn_ocr)
            if idx < 0:
                left_layout.addWidget(self.lbl_phase3_file_status)
            else:
                left_layout.insertWidget(idx, self.lbl_phase3_file_status)
        except Exception:
            pass

        try:
            self.lbl_phase3_review_summary = _Phase3QLabel("ตรวจสอบไฟล์นี้: -")
            self.lbl_phase3_review_summary.setWordWrap(True)
            self.lbl_phase3_review_summary.setStyleSheet("color:#57606a;font-weight:600;")

            self.chk_phase3_review_only = _Phase3QCheckBox("แสดงเฉพาะแถวที่ต้องแก้/ควรตรวจ")
            self.chk_phase3_review_only.setToolTip("ซ่อนแถวที่ผ่านแล้ว เพื่อให้ตรวจเฉพาะรายการสีแดง/เหลือง")
            self.chk_phase3_review_only.toggled.connect(lambda checked: _phase3_apply_row_review_state(self))

            btn_check = _Phase3QPushButton("ตรวจสอบข้อมูล")
            btn_check.clicked.connect(lambda: _phase3_apply_row_review_state(self, True))

            btn_next = _Phase3QPushButton("ไปจุดที่ต้องแก้ถัดไป")
            btn_next.clicked.connect(lambda: _phase3_focus_next_issue(self))

            toolbar = _Phase3QHBoxLayout()
            toolbar.addWidget(self.chk_phase3_review_only)
            toolbar.addWidget(btn_check)
            toolbar.addWidget(btn_next)
            toolbar.addStretch()

            right_layout = self.table.parentWidget().layout()
            table_idx = right_layout.indexOf(self.table)
            if table_idx < 0:
                right_layout.addWidget(self.lbl_phase3_review_summary)
                right_layout.addLayout(toolbar)
            else:
                right_layout.insertWidget(table_idx + 1, self.lbl_phase3_review_summary)
                right_layout.insertLayout(table_idx + 2, toolbar)
        except Exception as exc:
            print("PHASE3 review toolbar disabled:", exc)

    ProcessTab._build = _phase3_build

    _phase3_original_load_folder = ProcessTab._load_folder

    def _phase3_load_folder(self, folder: str):
        _phase3_original_load_folder(self, folder)
        _phase3_update_file_status_summary(self)

    ProcessTab._load_folder = _phase3_load_folder

    _phase3_original_on_doc = ProcessTab._on_doc

    def _phase3_on_doc(self, doc):
        _phase3_original_on_doc(self, doc)
        _phase3_update_file_status_summary(self)
        if getattr(doc, "source_pdf", "") == getattr(self, "current_path", ""):
            _phase3_apply_row_review_state(self)

    ProcessTab._on_doc = _phase3_on_doc

    _phase3_original_load_doc = ProcessTab._load_doc

    def _phase3_load_doc(self, doc):
        _phase3_original_load_doc(self, doc)
        _phase3_apply_row_review_state(self)

    ProcessTab._load_doc = _phase3_load_doc

    _phase3_original_capture_table = ProcessTab._capture_table

    def _phase3_capture_table(self):
        _phase3_original_capture_table(self)
        _phase3_update_file_status_summary(self)

    ProcessTab._capture_table = _phase3_capture_table

except Exception as _phase3_review_error:
    print("PHASE3 REVIEW WORKFLOW PATCH disabled:", _phase3_review_error)
# === END PHASE3 REVIEW WORKFLOW PATCH ===

# === PHASE5 PROCESS CACHE TOOLS PATCH ===
try:
    import os as _phase5_os
    from pathlib import Path as _Phase5Path
    from PySide6.QtWidgets import QPushButton as _Phase5QPushButton
    from PySide6.QtWidgets import QHBoxLayout as _Phase5QHBoxLayout
    from PySide6.QtWidgets import QMessageBox as _Phase5QMessageBox
    from .. import ocr_cache as _phase5_ocr_cache
    from .. import queue_status as _phase5_queue_status
    from .. import audit_log as _phase5_audit_log
    def _phase5_selected_paths(self):
        items = self.lst.selectedItems() if hasattr(self, "lst") else []
        if not items and hasattr(self, "lst") and self.lst.currentItem():
            items = [self.lst.currentItem()]
        out = []
        for it in items:
            try:
                p = it.data(Qt.UserRole)
                if p:
                    out.append(str(p))
            except Exception:
                pass
        return out
    def _phase5_clear_selected_cache(self):
        paths = _phase5_selected_paths(self)
        if not paths:
            _Phase5QMessageBox.information(self, "ล้าง OCR Cache", "กรุณาเลือกไฟล์ PO ก่อน")
            return
        cust = self.cmb_customer.currentText() if hasattr(self, "cmb_customer") else ""
        removed = 0
        for p in paths:
            removed += _phase5_ocr_cache.delete_cache_for_file(p, cust or None)
        _phase5_audit_log.log_event("ocr_cache_clear_selected", customer=cust, count=removed, paths=paths)
        _Phase5QMessageBox.information(self, "ล้าง OCR Cache", f"ล้าง cache แล้ว {removed} รายการ\nครั้งถัดไปที่ OCR จะอ่านจากไฟล์จริงใหม่")
    def _phase5_open_cache_dir(self):
        folder = _Phase5Path(_phase5_ocr_cache.CACHE_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            _phase5_os.startfile(str(folder))  # type: ignore[attr-defined]
        except Exception as exc:
            _Phase5QMessageBox.warning(self, "เปิด OCR Cache", str(exc))
    _phase5_original_build = ProcessTab._build
    def _phase5_build(self):
        _phase5_original_build(self)
        try:
            btn_clear = _Phase5QPushButton("ล้าง OCR Cache ของไฟล์ที่เลือก")
            btn_clear.setToolTip("ใช้เมื่อไฟล์ PDF เปลี่ยน หรือ Product Details เปลี่ยน แล้วต้องการ OCR ใหม่จากต้นฉบับ")
            btn_clear.clicked.connect(lambda: _phase5_clear_selected_cache(self))
            btn_cache = _Phase5QPushButton("เปิด Cache")
            btn_cache.clicked.connect(lambda: _phase5_open_cache_dir(self))
            bar = _Phase5QHBoxLayout()
            bar.addWidget(btn_clear); bar.addWidget(btn_cache); bar.addStretch()
            left_layout = self.lst.parentWidget().layout()
            idx = left_layout.indexOf(self.btn_ocr)
            if idx < 0:
                left_layout.addLayout(bar)
            else:
                left_layout.insertLayout(idx + 1, bar)
        except Exception as exc:
            print("PHASE5 cache toolbar disabled:", exc)
    ProcessTab._build = _phase5_build
    if hasattr(ProcessTab, "delete_selected_po_files"):
        _phase5_original_delete_po = ProcessTab.delete_selected_po_files
        def _phase5_delete_selected_po_files(self):
            paths = _phase5_selected_paths(self)
            _phase5_original_delete_po(self)
            cust = self.cmb_customer.currentText() if hasattr(self, "cmb_customer") else ""
            for p in paths:
                try:
                    if not _Phase5Path(p).exists():
                        _phase5_queue_status.set_status(p, "deleted", customer=cust, message="ลบไฟล์ PO แล้ว")
                        _phase5_audit_log.log_event("po_deleted", customer=cust, path=p)
                except Exception:
                    pass
        ProcessTab.delete_selected_po_files = _phase5_delete_selected_po_files
except Exception as _phase5_process_error:
    print("PHASE5 PROCESS CACHE TOOLS PATCH disabled:", _phase5_process_error)
# === END PHASE5 PROCESS CACHE TOOLS PATCH ===

# === HOTFIX_PROCESS_TABLE_CONTRAST_START ===
# Hotfix: improve text contrast in Process PO table for dark mode + highlighted rows.
# This block is intentionally appended so it can work with existing Phase patches without
# rewriting the whole file.
try:
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QColor, QBrush
except Exception:  # pragma: no cover
    QTimer = None


def _ocr_hf_color_luma(color):
    try:
        return (0.299 * color.red()) + (0.587 * color.green()) + (0.114 * color.blue())
    except Exception:
        return 0


def _ocr_hf_num(value):
    try:
        return float(str(value or "").replace(",", "").strip() or 0)
    except Exception:
        return 0.0


def _ocr_hf_widget_value(widget):
    if widget is None:
        return ""
    for attr in ("value", "currentText", "text"):
        try:
            fn = getattr(widget, attr, None)
            if callable(fn):
                return str(fn()).strip()
        except Exception:
            pass
    return ""


def _ocr_hf_cell_text(table, row, col):
    try:
        item = table.item(row, col)
        if item is not None:
            return item.text().strip()
    except Exception:
        pass
    try:
        return _ocr_hf_widget_value(table.cellWidget(row, col))
    except Exception:
        return ""


def _ocr_hf_row_severity(self, row):
    """Return 'critical', 'review', or ''."""
    table = getattr(self, "table", None)
    if table is None:
        return ""

    # Text-based signals from status/result columns.
    joined = " ".join(
        _ocr_hf_cell_text(table, row, c) for c in range(table.columnCount())
    ).lower()
    if any(k in joined for k in (
        "ต้องแก้", "ไม่มี", "missing", "critical", "error", "failed", "fail",
        "ห้าม export", "แก้ก่อน export",
    )):
        return "critical"
    if any(k in joined for k in (
        "ควรตรวจ", "review", "warning", "warn", "fuzzy", "ไม่มั่นใจ", "ตรวจทาน",
    )):
        return "review"

    # Structural validation fallback using known column constants.
    try:
        tmc_value = _ocr_hf_widget_value(table.cellWidget(row, C_TMC))
        stock_value = _ocr_hf_widget_value(table.cellWidget(row, C_STOCK))
        qty = _ocr_hf_num(_ocr_hf_cell_text(table, row, C_QTY))
        price = _ocr_hf_num(_ocr_hf_cell_text(table, row, C_PRICE))
        if not tmc_value or not stock_value or qty <= 0 or price <= 0:
            return "critical"
    except Exception:
        pass

    # If any existing row background is a light warning color, force dark text.
    try:
        for c in range(table.columnCount()):
            item = table.item(row, c)
            if item is None:
                continue
            bg = item.background().color()
            if bg.isValid() and bg.alpha() > 0 and _ocr_hf_color_luma(bg) > 165:
                return "review"
    except Exception:
        pass
    return ""


def _ocr_hf_set_combo_contrast(widget):
    if widget is None:
        return
    try:
        base = widget.styleSheet() or ""
        marker = "/* OCR_HF_COMBO_CONTRAST */"
        if marker not in base:
            widget.setStyleSheet(base + """
/* OCR_HF_COMBO_CONTRAST */
QComboBox {
    background-color: #303030;
    color: #ffffff;
    border: 1px solid #6b7280;
    padding: 2px 6px;
}
QComboBox QAbstractItemView {
    background-color: #202020;
    color: #ffffff;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}
""")
    except Exception:
        pass


def _ocr_hf_apply_row_contrast(self, row):
    table = getattr(self, "table", None)
    if table is None or row < 0 or row >= table.rowCount():
        return

    severity = _ocr_hf_row_severity(self, row)
    if severity == "critical":
        bg = QColor("#ffd6d6")      # soft red
        fg = QColor("#111827")      # near black
        status_fg = QColor("#b91c1c")
    elif severity == "review":
        bg = QColor("#fff1b8")      # soft yellow
        fg = QColor("#111827")
        status_fg = QColor("#92400e")
    else:
        bg = None
        fg = None
        status_fg = QColor("#e5e7eb")

    for c in range(table.columnCount()):
        item = table.item(row, c)
        if item is None:
            continue
        try:
            if bg is not None:
                item.setBackground(QBrush(bg))
                item.setForeground(QBrush(fg))
            else:
                # Keep normal dark-mode rows as-is, but fix any light background left by other patches.
                cur_bg = item.background().color()
                if cur_bg.isValid() and cur_bg.alpha() > 0 and _ocr_hf_color_luma(cur_bg) > 165:
                    item.setForeground(QBrush(QColor("#111827")))
        except Exception:
            pass

    # Make the result/status column extra readable.
    try:
        st = table.item(row, C_STATUS)
        if st is not None:
            st.setForeground(QBrush(status_fg))
            if severity == "critical":
                st.setToolTip(st.text())
    except Exception:
        pass

    # Cell widgets do not inherit item foreground/background, so style them separately.
    try:
        _ocr_hf_set_combo_contrast(table.cellWidget(row, C_TMC))
        _ocr_hf_set_combo_contrast(table.cellWidget(row, C_STOCK))
    except Exception:
        pass


def _ocr_hf_apply_table_contrast(self):
    table = getattr(self, "table", None)
    if table is None:
        return
    try:
        marker = "/* OCR_HF_TABLE_CONTRAST */"
        base = table.styleSheet() or ""
        if marker not in base:
            table.setStyleSheet(base + """
/* OCR_HF_TABLE_CONTRAST */
QTableWidget {
    background-color: #262626;
    alternate-background-color: #303030;
    gridline-color: #555555;
}
QHeaderView::section {
    background-color: #3a3a3a;
    color: #ffffff;
    font-weight: 600;
    border: 1px solid #555555;
    padding: 4px;
}
QTableWidget::item:selected {
    background-color: #2563eb;
    color: #ffffff;
}
""")
    except Exception:
        pass

    try:
        for r in range(table.rowCount()):
            _ocr_hf_apply_row_contrast(self, r)
        table.viewport().update()
    except Exception:
        pass


def _ocr_hf_enable_contrast_timer(self):
    if QTimer is None:
        return
    try:
        if getattr(self, "_ocr_hf_contrast_timer", None) is not None:
            return
        timer = QTimer(self)
        timer.setInterval(900)
        timer.timeout.connect(lambda: _ocr_hf_apply_table_contrast(self))
        timer.start()
        self._ocr_hf_contrast_timer = timer
    except Exception:
        pass


try:
    if not getattr(ProcessTab, "_ocr_hf_process_table_contrast_patched", False):
        _ocr_hf_original_build = ProcessTab._build

        def _ocr_hf_build(self, *args, **kwargs):
            result = _ocr_hf_original_build(self, *args, **kwargs)
            _ocr_hf_apply_table_contrast(self)
            _ocr_hf_enable_contrast_timer(self)
            return result

        ProcessTab._build = _ocr_hf_build

        def _ocr_hf_wrap_method(method_name):
            original = getattr(ProcessTab, method_name, None)
            if not callable(original):
                return
            def wrapped(self, *args, **kwargs):
                result = original(self, *args, **kwargs)
                try:
                    _ocr_hf_apply_table_contrast(self)
                except Exception:
                    pass
                return result
            setattr(ProcessTab, method_name, wrapped)

        for _ocr_hf_name in (
            "_append_row", "_load_doc", "_on_doc", "_on_finish", "delete_rows",
            "_recalc_totals", "_recalc_from_fields", "_capture_table",
            "run_validation", "validate_current", "validate_current_table",
            "_validate_current", "_phase3_validate_table", "_phase3_apply_review_filter",
            "_apply_review_filter", "_show_review_only", "_refresh_review_view",
        ):
            _ocr_hf_wrap_method(_ocr_hf_name)

        ProcessTab._ocr_hf_process_table_contrast_patched = True
except Exception:
    # Never let a contrast-only hotfix prevent the app from starting.
    pass
# === HOTFIX_PROCESS_TABLE_CONTRAST_END ===

# === PHASE9 AMOUNT/TOTAL REPAIR UI PATCH ===
try:
    from .. import amount_repair as _phase9_amount_repair
except Exception:  # pragma: no cover
    _phase9_amount_repair = None

# Add an explicit Amount OCR column so users can see why the system repairs price.
COLS = [
    "ลำดับ",
    "รหัสสินค้า (OCR)",
    "ชื่อสินค้า (OCR)",
    "tmc_code",
    "ชื่อสินค้าที่จับคู่ (TMC)",
    "",
    "จำนวน",
    "ราคา",
    "ยอดเงิน OCR",
    "ผลจับคู่",
]
C_ITEM, C_CODE, C_DESC, C_TMC, C_MATCH, C_STOCK, C_QTY, C_PRICE, C_AMOUNT, C_STATUS = range(10)


def _phase9_text_item(text, editable=True, color=None):
    item = QTableWidgetItem(str(text or ""))
    if not editable:
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    if color:
        item.setForeground(QBrush(QColor(color)))
    return item


def _phase9_fmt(v):
    try:
        f = float(str(v or "0").replace(",", ""))
    except Exception:
        f = 0.0
    if abs(f - round(f)) < 0.00001:
        return str(int(round(f)))
    return ("%.4f" % f).rstrip("0").rstrip(".")


def _phase9_append_row(self, line):
    # Ensure price/amount are consistent before showing the row.
    if _phase9_amount_repair is not None:
        try:
            _phase9_amount_repair.repair_line(line)
        except Exception:
            pass
    r = self.table.rowCount()
    self.table.insertRow(r)
    self.table.setItem(r, C_ITEM, _phase9_text_item(getattr(line, "item_no", "")))
    self.table.setItem(r, C_CODE, _phase9_text_item(getattr(line, "product_code_raw", "")))
    self.table.setItem(r, C_DESC, _phase9_text_item(getattr(line, "description_raw", "")))

    tmc = TmcCombo(self.ctx.tmc_items(), getattr(line, "tmc_code", ""))
    self.table.setCellWidget(r, C_TMC, tmc)

    mn = _phase9_text_item(getattr(line, "matched_name", ""), editable=False, color="#0a58ca")
    self.table.setItem(r, C_MATCH, mn)
    try:
        tmc.currentTextChanged.connect(self._on_tmc_changed)
    except Exception:
        pass

    stock = SearchCombo(self.ctx.stock_group_codes, getattr(line, "", ""))
    self.table.setCellWidget(r, C_STOCK, stock)

    self.table.setItem(r, C_QTY, _phase9_text_item(_phase9_fmt(getattr(line, "qty", 0))))
    self.table.setItem(r, C_PRICE, _phase9_text_item(_phase9_fmt(getattr(line, "price", 0))))
    self.table.setItem(r, C_AMOUNT, _phase9_text_item(_phase9_fmt(getattr(line, "amount", 0)), editable=True, color="#005f73"))

    st_text = f"{getattr(line, 'match_status', '')} {float(getattr(line, 'match_score', 0) or 0):.0f}".strip()
    st = _phase9_text_item(st_text, editable=False)
    try:
        st.setForeground(QBrush(self._status_color(getattr(line, "match_status", ""))))
    except Exception:
        pass
    self.table.setItem(r, C_STATUS, st)


def _phase9_capture_table(self):
    """Read the visible table back into the current document, including Amount OCR."""
    if not getattr(self, "current_path", "") or self.current_path not in getattr(self, "docs", {}):
        return
    doc = self.docs[self.current_path]
    doc.po_no = self.ed_po.text().strip()
    doc.po_date = self.ed_date.text().strip()
    doc.total = self._num(self.ed_total.text())
    doc.vat = self._num(self.ed_vat.text())
    doc.grand_total = self._num(self.ed_grand.text())
    lines = []
    for r in range(self.table.rowCount()):
        tmc_w = self.table.cellWidget(r, C_TMC)
        stock_w = self.table.cellWidget(r, C_STOCK)
        line = POLine(
            item_no=self._cell(r, C_ITEM),
            product_code_raw=self._cell(r, C_CODE),
            description_raw=self._cell(r, C_DESC),
            tmc_code=tmc_w.value() if tmc_w and hasattr(tmc_w, "value") else "",
            matched_name=self._cell(r, C_MATCH),
            stock_group_code=stock_w.value() if stock_w and hasattr(stock_w, "value") else "",
            qty=self._num(self._cell(r, C_QTY)),
            price=self._num(self._cell(r, C_PRICE)),
            amount=self._num(self._cell(r, C_AMOUNT)),
        )
        if _phase9_amount_repair is not None:
            try:
                _phase9_amount_repair.repair_line(line)
            except Exception:
                pass
        lines.append(line)
    doc.lines = lines


def _phase9_recalc_totals(self):
    """Recalculate totals from Amount OCR when available; otherwise qty*price."""
    total = 0.0
    for r in range(self.table.rowCount()):
        amount = self._num(self._cell(r, C_AMOUNT))
        qty = self._num(self._cell(r, C_QTY))
        price = self._num(self._cell(r, C_PRICE))
        if amount > 0:
            total += amount
        else:
            total += qty * price
    total = round(total, 2)
    vat = round(total * 0.07, 2)
    self.ed_total.setText(f"{total:.2f}")
    self.ed_vat.setText(f"{vat:.2f}")
    self.ed_grand.setText(f"{total + vat:.2f}")


_phase9_original_load_doc = getattr(ProcessTab, "_load_doc", None)
def _phase9_load_doc(self, doc):
    if _phase9_amount_repair is not None:
        try:
            _phase9_amount_repair.repair_document(doc, update_header=True)
        except Exception:
            pass
    return _phase9_original_load_doc(self, doc)


_phase9_original_on_doc = getattr(ProcessTab, "_on_doc", None)
def _phase9_on_doc(self, doc):
    if _phase9_amount_repair is not None:
        try:
            _phase9_amount_repair.repair_document(doc, update_header=True)
        except Exception:
            pass
    return _phase9_original_on_doc(self, doc)


_phase9_original_save_current = getattr(ProcessTab, "save_current", None)
def _phase9_save_current(self):
    # Repair and update visible totals right before any validation/export wrapper runs.
    try:
        self._capture_table()
        if getattr(self, "current_path", "") in getattr(self, "docs", {}):
            doc = self.docs[self.current_path]
            if _phase9_amount_repair is not None:
                _phase9_amount_repair.repair_document(doc, update_header=True)
            self._recalc_totals()
    except Exception:
        pass
    return _phase9_original_save_current(self)


_phase9_original_save_combined = getattr(ProcessTab, "save_combined", None)
def _phase9_save_combined(self):
    try:
        self._capture_table()
        for doc in getattr(self, "docs", {}).values():
            if _phase9_amount_repair is not None:
                _phase9_amount_repair.repair_document(doc, update_header=True)
    except Exception:
        pass
    return _phase9_original_save_combined(self)


# Apply monkey patches. They run after the class is defined and before MainWindow
# creates the tab, so _build() will use the new COLS/constants.
ProcessTab._append_row = _phase9_append_row
ProcessTab._capture_table = _phase9_capture_table
ProcessTab._recalc_totals = _phase9_recalc_totals
if _phase9_original_load_doc is not None:
    ProcessTab._load_doc = _phase9_load_doc
if _phase9_original_on_doc is not None:
    ProcessTab._on_doc = _phase9_on_doc
if _phase9_original_save_current is not None:
    ProcessTab.save_current = _phase9_save_current
if _phase9_original_save_combined is not None:
    ProcessTab.save_combined = _phase9_save_combined

# === PHASE10 SOFT REMOVE + OCR STABILITY PATCH ===
try:
    import shutil as _phase10_shutil
    from datetime import datetime as _phase10_datetime
    from pathlib import Path as _phase10_Path
    from PySide6.QtCore import Qt as _phase10_Qt
    from PySide6.QtWidgets import QMessageBox as _phase10_QMessageBox, QTableWidgetItem as _phase10_QTableWidgetItem
    from app.ocr_stability import normalize_po_document as _phase10_normalize_doc, to_float as _phase10_to_float

    def _phase10_col_by_header(self, names):
        names = [str(n).lower() for n in names]
        for c in range(self.table.columnCount()):
            h = self.table.horizontalHeaderItem(c)
            text = (h.text() if h else '').lower()
            if any(n in text for n in names):
                return c
        return -1

    def _phase10_cell_text(self, r, c):
        if c < 0:
            return ''
        it = self.table.item(r, c)
        return it.text().strip() if it else ''

    def _phase10_set_cell(self, r, c, value):
        if c < 0:
            return
        it = self.table.item(r, c)
        if it is None:
            it = _phase10_QTableWidgetItem()
            self.table.setItem(r, c, it)
        if isinstance(value, (int, float)):
            it.setText(('%g' % value) if value else '0')
        else:
            it.setText(str(value))

    def _phase10_repair_table_amount_price(self):
        c_qty = _phase10_col_by_header(self, ['จำนวน', 'qty'])
        c_price = _phase10_col_by_header(self, ['ราคา', 'unit price', 'price'])
        c_amount = _phase10_col_by_header(self, ['ยอดเงิน', 'amount'])
        if c_qty < 0 or c_price < 0:
            return False
        changed = False
        for r in range(self.table.rowCount()):
            qty = _phase10_to_float(_phase10_cell_text(self, r, c_qty))
            price = _phase10_to_float(_phase10_cell_text(self, r, c_price))
            amount = _phase10_to_float(_phase10_cell_text(self, r, c_amount)) if c_amount >= 0 else 0.0
            if qty > 0 and amount > 0:
                repaired_price = round(amount / qty, 2)
                if price <= 0 or abs(qty * price - amount) > max(1.0, amount * 0.015):
                    _phase10_set_cell(self, r, c_price, repaired_price)
                    changed = True
            elif qty > 0 and price > 0 and c_amount >= 0:
                current_amount_text = _phase10_cell_text(self, r, c_amount)
                if not current_amount_text or _phase10_to_float(current_amount_text) <= 0:
                    _phase10_set_cell(self, r, c_amount, round(qty * price, 2))
                    changed = True
        return changed

    def _phase10_compute_table_total(self):
        c_qty = _phase10_col_by_header(self, ['จำนวน', 'qty'])
        c_price = _phase10_col_by_header(self, ['ราคา', 'unit price', 'price'])
        c_amount = _phase10_col_by_header(self, ['ยอดเงิน', 'amount'])
        total = 0.0
        for r in range(self.table.rowCount()):
            amount = _phase10_to_float(_phase10_cell_text(self, r, c_amount)) if c_amount >= 0 else 0.0
            if amount > 0:
                total += amount
            else:
                qty = _phase10_to_float(_phase10_cell_text(self, r, c_qty))
                price = _phase10_to_float(_phase10_cell_text(self, r, c_price))
                total += qty * price
        return round(total, 2)

    _phase10_orig_build = ProcessTab._build
    def _phase10_build(self, *a, **kw):
        _phase10_orig_build(self, *a, **kw)
        try:
            if hasattr(self, 'btn_delete_po'):
                self.btn_delete_po.setText('🗂 เอาไฟล์ PO ออกจากรายการ')
                self.btn_delete_po.setToolTip('ย้ายไฟล์ไปโฟลเดอร์ _REMOVED_FROM_OCR โดยไม่ลบไฟล์จริงออกจากเครื่อง')
        except Exception:
            pass
    ProcessTab._build = _phase10_build

    def _phase10_soft_remove_selected_po_files(self):
        """Soft-remove selected PO files: move to _REMOVED_FROM_OCR, never delete."""
        try:
            if self.worker is not None and hasattr(self.worker, 'isRunning') and self.worker.isRunning():
                _phase10_QMessageBox.warning(self, 'เอาไฟล์ PO ออกจากรายการ', 'ระบบกำลังอ่านเอกสาร OCR อยู่ กรุณารอให้เสร็จก่อน')
                return
        except Exception:
            pass
        selected = self.lst.selectedItems()
        if not selected and self.lst.currentItem():
            selected = [self.lst.currentItem()]
        if not selected:
            _phase10_QMessageBox.information(self, 'เอาไฟล์ PO ออกจากรายการ', 'กรุณาเลือกไฟล์ PO ก่อน')
            return
        rows = []
        seen = set()
        for item in selected:
            raw = item.data(_phase10_Qt.UserRole)
            if not raw:
                continue
            p = _phase10_Path(str(raw))
            key = str(p.resolve()) if p.exists() else str(p)
            if key not in seen:
                seen.add(key)
                rows.append((item, p, str(raw)))
        if not rows:
            return
        names = [p.name for _i, p, _r in rows]
        preview = '\n'.join(names[:10])
        if len(names) > 10:
            preview += f'\n...อีก {len(names) - 10} ไฟล์'
        ans = _phase10_QMessageBox.question(
            self,
            'ยืนยันเอาไฟล์ PO ออกจากรายการ',
            f'ต้องการเอาไฟล์ PO ที่เลือก {len(rows)} ไฟล์ออกจากรายการ OCR หรือไม่?\n\n{preview}\n\n'
            'ระบบจะไม่ลบไฟล์จริง แต่จะย้ายไปโฟลเดอร์ _REMOVED_FROM_OCR ในโฟลเดอร์ PO เดิม\n'
            'ถ้าต้องใช้ใหม่ สามารถย้ายไฟล์กลับมาได้',
        )
        if ans != _phase10_QMessageBox.Yes:
            return
        moved_keys, failed = set(), []
        for _item, path, raw in rows:
            try:
                if path.exists():
                    archive = path.parent / '_REMOVED_FROM_OCR'
                    archive.mkdir(parents=True, exist_ok=True)
                    target = archive / path.name
                    if target.exists():
                        target = archive / f'{path.stem}_{_phase10_datetime.now().strftime("%Y%m%d_%H%M%S")}{path.suffix}'
                    _phase10_shutil.move(str(path), str(target))
                    moved_keys.add(raw)
                    moved_keys.add(str(path))
                    try:
                        moved_keys.add(str(path.resolve()))
                    except Exception:
                        pass
                    try:
                        self.docs.pop(raw, None); self.docs.pop(str(path), None)
                    except Exception:
                        pass
            except Exception as exc:
                failed.append(f'{path.name}: {exc}')
        for row in range(self.lst.count() - 1, -1, -1):
            item = self.lst.item(row)
            raw = item.data(_phase10_Qt.UserRole)
            if not raw:
                continue
            p = _phase10_Path(str(raw))
            keys = {str(raw), str(p)}
            try:
                keys.add(str(p.resolve()))
            except Exception:
                pass
            if keys & moved_keys:
                self.lst.takeItem(row)
        if self.current_path:
            cur = _phase10_Path(str(self.current_path))
            keys = {str(self.current_path), str(cur)}
            try:
                keys.add(str(cur.resolve()))
            except Exception:
                pass
            if keys & moved_keys:
                self.current_path = ''
                try:
                    self._clear_table()
                except Exception:
                    pass
        msg = f'เอาไฟล์ออกจากรายการสำเร็จ {len(rows) - len(failed)} ไฟล์\nไฟล์จริงถูกเก็บไว้ใน _REMOVED_FROM_OCR'
        if failed:
            msg += '\n\nทำไม่สำเร็จ:\n' + '\n'.join(failed[:10])
            _phase10_QMessageBox.warning(self, 'เอาไฟล์ PO ออกจากรายการ', msg)
        else:
            _phase10_QMessageBox.information(self, 'เอาไฟล์ PO ออกจากรายการ', msg)
    ProcessTab.delete_selected_po_files = _phase10_soft_remove_selected_po_files

    _phase10_orig_load_doc = ProcessTab._load_doc
    def _phase10_load_doc(self, doc):
        try:
            _phase10_normalize_doc(doc)
        except Exception:
            pass
        return _phase10_orig_load_doc(self, doc)
    ProcessTab._load_doc = _phase10_load_doc

    _phase10_orig_capture = ProcessTab._capture_table
    def _phase10_capture_table(self):
        try:
            _phase10_repair_table_amount_price(self)
        except Exception:
            pass
        result = _phase10_orig_capture(self)
        try:
            if getattr(self, 'current_path', '') and self.current_path in self.docs:
                _phase10_normalize_doc(self.docs[self.current_path])
        except Exception:
            pass
        return result
    ProcessTab._capture_table = _phase10_capture_table

    def _phase10_recalc_totals(self):
        try:
            _phase10_repair_table_amount_price(self)
            total = _phase10_compute_table_total(self)
        except Exception:
            total = 0.0
            for r in range(self.table.rowCount()):
                total += self._num(self._cell(r, C_QTY)) * self._num(self._cell(r, C_PRICE))
        vat = round(total * 0.07, 2)
        self.ed_total.setText(f'{total:.2f}')
        self.ed_vat.setText(f'{vat:.2f}')
        self.ed_grand.setText(f'{total + vat:.2f}')
    ProcessTab._recalc_totals = _phase10_recalc_totals

    _phase10_orig_save_current = ProcessTab.save_current
    def _phase10_save_current(self):
        try:
            _phase10_repair_table_amount_price(self)
            self._recalc_totals()
        except Exception:
            pass
        return _phase10_orig_save_current(self)
    ProcessTab.save_current = _phase10_save_current

    _phase10_orig_save_combined = ProcessTab.save_combined
    def _phase10_save_combined(self):
        try:
            _phase10_repair_table_amount_price(self)
            self._recalc_totals()
        except Exception:
            pass
        return _phase10_orig_save_combined(self)
    ProcessTab.save_combined = _phase10_save_combined

except Exception as _phase10_exc:
    print('Phase10 soft remove / OCR stability patch skipped:', _phase10_exc)
# === END PHASE10 PATCH ===

# === PHASE12 CLEANUP DELETE ARABIC PATCH ===
try:
    from pathlib import Path as _Phase12Path
    import shutil as _phase12_shutil
    from datetime import datetime as _Phase12DateTime
    from PySide6.QtWidgets import QPushButton as _Phase12Button, QMessageBox as _Phase12Msg, QTableWidgetItem as _Phase12Item
    from .widgets import SearchCombo as _Phase12SearchCombo
    from ..arabic_digits import normalize_obj_digits as _phase12_norm_doc, to_arabic_digits as _phase12_digits

    def _phase12_hide_stock_column(self):
        try:
            self.table.setColumnHidden(C_STOCK, True)
            self.table.setHorizontalHeaderItem(C_STOCK, _Phase12Item(""))
        except Exception:
            pass

    def _phase12_clear_cache_dirs(self):
        roots = []
        try:
            roots.append(_Phase12Path(self.ctx.cfg.get("root_folder", "")))
        except Exception:
            pass
        roots.append(_Phase12Path.cwd())
        roots.append(_Phase12Path(__file__).resolve().parents[2])
        candidates = []
        names = {".ocr_cache", "ocr_cache", "OCR_CACHE", "_OCR_CACHE", "OCR Cache", "CACHE", "cache"}
        for root in roots:
            if not root or not root.exists():
                continue
            for name in names:
                p = root / name
                if p.exists() and p.is_dir() and "__pycache__" not in p.name.lower():
                    candidates.append(p)
            # One-level scan only, avoid deleting unrelated deep folders.
            try:
                for p in root.iterdir():
                    if p.is_dir() and "cache" in p.name.lower() and "pycache" not in p.name.lower():
                        if "ocr" in p.name.lower() or p.name.upper() in {"CACHE", "OCR_CACHE", "_OCR_CACHE"}:
                            candidates.append(p)
            except Exception:
                pass
        # unique
        uniq = []
        seen = set()
        for p in candidates:
            try:
                key = str(p.resolve())
            except Exception:
                key = str(p)
            if key not in seen:
                seen.add(key); uniq.append(p)
        if not uniq:
            _Phase12Msg.information(self, "ล้าง OCR Cache ทั้งหมด", "ไม่พบโฟลเดอร์ OCR Cache")
            return
        msg = "พบโฟลเดอร์ Cache ที่จะล้าง:\n" + "\n".join(str(x) for x in uniq[:10])
        if len(uniq) > 10:
            msg += f"\n...และอีก {len(uniq)-10} โฟลเดอร์"
        if _Phase12Msg.question(self, "ยืนยันล้าง OCR Cache ทั้งหมด", msg) != _Phase12Msg.Yes:
            return
        removed = 0; failed = []
        for p in uniq:
            try:
                for child in p.iterdir():
                    if child.is_dir():
                        _phase12_shutil.rmtree(child)
                    else:
                        child.unlink()
                    removed += 1
            except Exception as exc:
                failed.append(f"{p}: {exc}")
        if failed:
            _Phase12Msg.warning(self, "ล้าง OCR Cache", f"ล้างสำเร็จบางส่วน {removed} รายการ\n\n" + "\n".join(failed[:8]))
        else:
            _Phase12Msg.information(self, "ล้าง OCR Cache", f"ล้าง OCR Cache ทั้งหมดแล้ว ({removed} รายการ)")

    def _phase12_soft_remove_po_files(self):
        """Move selected PO PDFs out of OCR list without permanently deleting them."""
        try:
            if self.worker is not None and hasattr(self.worker, "isRunning") and self.worker.isRunning():
                _Phase12Msg.warning(self, "เอาไฟล์ PO ออกจากรายการ", "ระบบกำลังอ่านเอกสาร OCR อยู่ กรุณารอให้เสร็จก่อน")
                return
        except Exception:
            pass
        selected = self.lst.selectedItems() if hasattr(self, "lst") else []
        if not selected and getattr(self, "lst", None) and self.lst.currentItem():
            selected = [self.lst.currentItem()]
        if not selected:
            _Phase12Msg.information(self, "เอาไฟล์ PO ออกจากรายการ", "กรุณาเลือกไฟล์ PO ก่อน")
            return
        items = []
        for item in selected:
            raw = item.data(Qt.UserRole)
            if raw:
                items.append((item, _Phase12Path(str(raw))))
        if not items:
            return
        preview = "\n".join(p.name for _, p in items[:10])
        if _Phase12Msg.question(
            self,
            "ยืนยันเอาไฟล์ออกจากรายการ OCR",
            f"ต้องการเอาไฟล์ PO ที่เลือก {len(items)} ไฟล์ออกจากรายการ OCR หรือไม่?\n\n{preview}\n\n"
            "ระบบจะไม่ลบทิ้งถาวร แต่จะย้ายไปโฟลเดอร์ _REMOVED_FROM_OCR",
        ) != _Phase12Msg.Yes:
            return
        moved_raw = set(); failed = []
        for _item, p in items:
            try:
                if p.exists():
                    dest_dir = p.parent / "_REMOVED_FROM_OCR"
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest = dest_dir / p.name
                    if dest.exists():
                        dest = dest_dir / f"{p.stem}_{_Phase12DateTime.now().strftime('%Y%m%d_%H%M%S')}{p.suffix}"
                    _phase12_shutil.move(str(p), str(dest))
                moved_raw.add(str(p))
                try: moved_raw.add(str(p.resolve()))
                except Exception: pass
                if hasattr(self, "docs"):
                    self.docs.pop(str(p), None)
            except Exception as exc:
                failed.append(f"{p.name}: {exc}")
        for row in range(self.lst.count() - 1, -1, -1):
            item = self.lst.item(row)
            raw = item.data(Qt.UserRole)
            if not raw: continue
            keys = {str(raw), str(_Phase12Path(str(raw)))}
            try: keys.add(str(_Phase12Path(str(raw)).resolve()))
            except Exception: pass
            if keys & moved_raw:
                self.lst.takeItem(row)
        self.current_path = ""
        try: self._clear_table()
        except Exception: pass
        if failed:
            _Phase12Msg.warning(self, "เอาไฟล์ออกจากรายการ", "ดำเนินการบางส่วนไม่สำเร็จ:\n" + "\n".join(failed[:10]))
        else:
            _Phase12Msg.information(self, "เอาไฟล์ออกจากรายการ", f"เอาไฟล์ออกจากรายการ OCR แล้ว {len(items)} ไฟล์")

    def _phase12_after_build_process(self):
        _phase12_hide_stock_column(self)
        try:
            parent = self.btn_ocr.parentWidget()
            lay = parent.layout()
            if not getattr(self, "_phase12_clear_all_cache_added", False):
                btn = _Phase12Button("🧹 ล้าง OCR Cache ทั้งหมด")
                btn.setToolTip("ล้างผล OCR ที่จำไว้ทั้งหมด แล้วให้อ่านไฟล์ใหม่จาก PDF จริง")
                btn.clicked.connect(self.clear_all_ocr_cache)
                lay.addWidget(btn)
                self._phase12_clear_all_cache_added = True
        except Exception:
            pass

    def _phase12_build_process(self, *_a, **_kw):
        _phase12_old_process_build(self, *_a, **_kw)
        _phase12_after_build_process(self)

    def _phase12_load_doc(self, doc, *_a, **_kw):
        try: _phase12_norm_doc(doc)
        except Exception: pass
        res = _phase12_old_load_doc(self, doc, *_a, **_kw)
        _phase12_hide_stock_column(self)
        return res

    def _phase12_capture_table(self, *_a, **_kw):
        res = _phase12_old_capture_table(self, *_a, **_kw)
        try:
            if self.current_path and self.current_path in self.docs:
                _phase12_norm_doc(self.docs[self.current_path])
        except Exception:
            pass
        return res

    def _phase12_append_row(self, line, *_a, **_kw):
        try: _phase12_norm_doc(line)
        except Exception: pass
        # Avoid forcing users to fill stock_group_code.
        try:
            line.stock_group_code = ""
        except Exception:
            pass
        res = _phase12_old_append_row(self, line, *_a, **_kw)
        _phase12_hide_stock_column(self)
        return res

    def _phase12_recalc_totals(self, *_a, **_kw):
        res = _phase12_old_recalc_totals(self, *_a, **_kw)
        for e in (getattr(self, "ed_po", None), getattr(self, "ed_date", None), getattr(self, "ed_total", None), getattr(self, "ed_vat", None), getattr(self, "ed_grand", None)):
            try: e.setText(_phase12_digits(e.text()))
            except Exception: pass
        return res

    ProcessTab.clear_all_ocr_cache = _phase12_clear_cache_dirs
    ProcessTab.delete_selected_po_files = _phase12_soft_remove_po_files
    if not getattr(ProcessTab, "_phase12_process_patched", False):
        _phase12_old_process_build = ProcessTab._build
        _phase12_old_load_doc = ProcessTab._load_doc
        _phase12_old_capture_table = ProcessTab._capture_table
        _phase12_old_append_row = ProcessTab._append_row
        _phase12_old_recalc_totals = ProcessTab._recalc_totals
        ProcessTab._build = _phase12_build_process
        ProcessTab._load_doc = _phase12_load_doc
        ProcessTab._capture_table = _phase12_capture_table
        ProcessTab._append_row = _phase12_append_row
        ProcessTab._recalc_totals = _phase12_recalc_totals
        ProcessTab._phase12_process_patched = True
except Exception:
    pass
