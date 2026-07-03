"""Tab 0: register/manage customers and attach each customer's tmc_code mapping file."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QFileDialog, QMessageBox, QAbstractItemView,
    QHeaderView, QGroupBox,
)

from .. import customers
from ..context import AppContext

COLS = ["ลูกค้า", "ไฟล์ tmc_code", "จำนวนสินค้า (mapping)", "จำนวนไฟล์ PO", "เรียนรู้ตำแหน่งแล้ว"]


class CustomersTab(QWidget):
    def __init__(self, ctx: AppContext, on_changed=None):
        super().__init__()
        self.ctx = ctx
        self.on_changed = on_changed
        self._build()
        self.refresh()

    def _build(self):
        v = QVBoxLayout(self)

        add = QGroupBox("ลงทะเบียนลูกค้าใหม่")
        h = QHBoxLayout(add)
        h.addWidget(QLabel("ชื่อลูกค้า:"))
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("เช่น CMT, Ogihara ...")
        h.addWidget(self.ed_name, 1)
        b_create = QPushButton("➕ สร้างลูกค้า (สร้างโฟลเดอร์)")
        b_create.clicked.connect(self.create_customer)
        h.addWidget(b_create)
        v.addWidget(add)

        hint = QLabel("แต่ละลูกค้าใช้ชื่อสินค้าไม่เหมือนกัน จึงต้องแนบไฟล์ tmc_code "
                      "(Product Details.xlsx) ของลูกค้าแต่ละราย — เลือกลูกค้าในตารางแล้วกด "
                      "\"นำเข้าไฟล์ tmc_code\"")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#555;")
        v.addWidget(hint)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        b_import = QPushButton("📎 นำเข้าไฟล์ tmc_code (Product Details.xlsx)")
        b_import.clicked.connect(self.import_mapping)
        b_import_po = QPushButton("📥 นำเข้าไฟล์ PO")
        b_import_po.setToolTip("คัดลอกไฟล์ PDF เข้าโฟลเดอร์ PO ของลูกค้าที่เลือก")
        b_import_po.clicked.connect(self.import_po)
        row.addWidget(b_import_po)
        b_open = QPushButton("📂 เปิดโฟลเดอร์ลูกค้า")
        b_open.clicked.connect(self.open_folder)
        b_refresh = QPushButton("รีเฟรช")
        b_refresh.clicked.connect(self.refresh)
        row.addWidget(b_import); row.addWidget(b_open); row.addStretch(); row.addWidget(b_refresh)
        v.addLayout(row)

        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)

    # ---------- helpers ----------
    def _selected_customer(self) -> str:
        r = self.table.currentRow()
        if r < 0:
            return ""
        return self.table.item(r, 0).text()

    def refresh(self):
        self.table.setRowCount(0)
        for name in self.ctx.customers():
            info = customers.customer_status(self.ctx.cfg, name)
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = [
                name,
                Path(info["product_file"]).name if info["has_product_file"] else "— ยังไม่มี —",
                str(info["n_products"]) if info["has_product_file"] else "0",
                str(info["n_po"]),
                "✓" if info["has_template"] else "—",
            ]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c == 1 and not info["has_product_file"]:
                    it.setForeground(Qt.red)
                if c in (2, 3, 4):
                    it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

    # ---------- actions ----------
    def create_customer(self):
        name = self.ed_name.text().strip()
        if not name:
            QMessageBox.information(self, "ลงทะเบียนลูกค้า", "กรุณากรอกชื่อลูกค้า")
            return
        try:
            path = customers.create_customer(self.ctx.cfg, name)
        except customers.RegisterError as e:
            QMessageBox.warning(self, "ลงทะเบียนลูกค้า", str(e))
            return
        self.ed_name.clear()
        self.refresh()
        self._notify()
        r = QMessageBox.question(
            self, "สร้างลูกค้าแล้ว",
            f"สร้างโฟลเดอร์ลูกค้า '{name}' เรียบร้อย:\n{path}\n\n"
            f"ต้องการนำเข้าไฟล์ tmc_code (Product Details.xlsx) ของลูกค้านี้เลยหรือไม่?")
        if r == QMessageBox.Yes:
            self._select_customer(name)
            self.import_mapping()

    def import_mapping(self):
        customer = self._selected_customer()
        if not customer:
            QMessageBox.information(self, "นำเข้าไฟล์ tmc_code",
                                    "กรุณาเลือกลูกค้าในตารางก่อน")
            return
        src, _ = QFileDialog.getOpenFileName(
            self, f"เลือกไฟล์ tmc_code ของลูกค้า '{customer}'", "", "Excel (*.xlsx)")
        if not src:
            return
        ok, msg, _ = customers.validate_product_file(src)
        if not ok:
            QMessageBox.warning(self, "ไฟล์ไม่ถูกต้อง",
                                f"{msg}\n\nไฟล์ต้องมีคอลัมน์ 'ชื่อสินค้าลูกค้า' และ 'tmc_code'")
            return
        try:
            dest = customers.import_product_file(self.ctx.cfg, customer, src)
        except customers.RegisterError as e:
            QMessageBox.warning(self, "นำเข้าไฟล์", str(e))
            return
        # if this customer is currently active, reload its matcher
        if self.ctx.current_customer == customer:
            self.ctx.set_customer(customer)
        self.refresh()
        self._notify()
        self.lbl.setText(f"✔ นำเข้าไฟล์ tmc_code ของ '{customer}' แล้ว ({msg})\n→ {dest}")

    def import_po(self):
        customer = self._selected_customer()
        if not customer:
            QMessageBox.information(self, "นำเข้าไฟล์ PO", "กรุณาเลือกลูกค้าในตารางก่อน")
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, f"เลือกไฟล์ PO (PDF) ของลูกค้า '{customer}'", "", "PDF (*.pdf)")
        if not files:
            return
        try:
            copied, skipped = customers.import_po_files(self.ctx.cfg, customer, files)
        except customers.RegisterError as e:
            QMessageBox.warning(self, "นำเข้าไฟล์ PO", str(e))
            return
        self.refresh()
        self._notify()
        msg = f"นำเข้า {len(copied)} ไฟล์เข้าโฟลเดอร์ '{customer}' แล้ว"
        if skipped:
            msg += f"  |  ข้าม {len(skipped)} ไฟล์ (มีอยู่แล้ว)"
        self.lbl.setText("✔ " + msg)

    def open_folder(self):
        customer = self._selected_customer()
        if not customer:
            return
        path = Path(self.ctx.cfg["root_folder"]) / customer
        if path.exists():
            os.startfile(str(path))  # Windows

    def _select_customer(self, name: str):
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == name:
                self.table.selectRow(r)
                return

    def _notify(self):
        if self.on_changed:
            self.on_changed()
