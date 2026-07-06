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

COLS = ["ลำดับ", "รหัสลูกค้า (OCR)", "ชื่อสินค้า (OCR)",
        "tmc_code", "ชื่อสินค้าที่จับคู่ (TMC)", "stock_group_code", "จำนวน", "ราคา", "ผลจับคู่"]
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

        self.btn_delete_po = QPushButton("🗑 ลบไฟล์ PO ที่เลือก")
        self.btn_delete_po.setToolTip("ลบไฟล์ PDF ที่เลือกออกจากโฟลเดอร์ PO ของลูกค้า")
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

            f"ต้องการลบไฟล์ PO ที่เลือก {len(items_to_delete)} ไฟล์ออกจากโฟลเดอร์ลูกค้าหรือไม่?\n\n"

            f"{preview}\n\n"

            "หมายเหตุ: ระบบจะลบไฟล์ PDF จริงออกจากเครื่อง แต่ไม่ลบไฟล์ Excel ที่เคย Export แล้ว",

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
            require_stock_group=True,
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
            require_stock_group=True,
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
