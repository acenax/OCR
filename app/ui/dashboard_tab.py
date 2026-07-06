"""Dashboard tab for TMC OCR.

Safe replacement for the previous dashboard file that contained an unterminated
f-string around line 167.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QAbstractItemView,
)

from ..context import AppContext

try:
    from .. import mapping_memory
except Exception:
    mapping_memory = None

COLS = ["ลูกค้า", "Product Details", "ไฟล์ PO", "Excel Export", "Mapping Memory", "สถานะ/คำแนะนำ"]


class DashboardTab(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._build()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)

        title = QLabel("Dashboard ภาพรวมงาน OCR")
        title.setStyleSheet("font-size:20px;font-weight:700;")
        root.addWidget(title)

        self.lbl_summary = QLabel("-")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setStyleSheet(
            "padding:10px;border:1px solid #555;border-radius:8px;"
            "background:#2f2f2f;color:#ffffff;"
        )
        root.addWidget(self.lbl_summary)

        actions = QHBoxLayout()
        self.btn_refresh = QPushButton("↻ รีเฟรช Dashboard")
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_open_root = QPushButton("เปิดโฟลเดอร์หลัก")
        self.btn_open_root.clicked.connect(self.open_root_folder)
        actions.addWidget(self.btn_refresh)
        actions.addWidget(self.btn_open_root)
        actions.addStretch()
        root.addLayout(actions)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        root.addWidget(self.table, 1)

        note = QLabel(
            "หมายเหตุ: Dashboard นี้นับจากไฟล์ในโฟลเดอร์จริง เช่น PO PDF, "
            "Product Details และ Excel ที่ Export แล้ว เพื่อช่วยบอกว่างานไหนควรทำต่อก่อน"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#c9d1d9;")
        root.addWidget(note)

    def _cfg_path(self, customer: str, key: str) -> Path:
        root = Path(str(self.ctx.cfg.get("root_folder", "")))
        sub = str(self.ctx.cfg.get(key, ""))
        return root / customer / sub

    @staticmethod
    def _count_files(folder: Path, suffixes: tuple[str, ...]) -> int:
        if not folder.exists():
            return 0
        suffixes = tuple(s.lower() for s in suffixes)
        return sum(1 for p in folder.iterdir() if p.is_file() and p.suffix.lower() in suffixes)

    @staticmethod
    def _item(text: object, *, color: str | None = None) -> QTableWidgetItem:
        it = QTableWidgetItem(str(text))
        if color:
            it.setForeground(QBrush(QColor(color)))
        return it

    def refresh(self) -> None:
        try:
            customers = list(self.ctx.customers())
        except Exception:
            customers = []

        self.table.setRowCount(0)

        total_po = 0
        total_export = 0
        missing_mapping = 0
        ready_customers = 0
        memory_total = 0

        for customer in customers:
            po_dir = self._cfg_path(customer, "po_subfolder")
            product_dir = self._cfg_path(customer, "product_subfolder")
            invoice_dir = self._cfg_path(customer, "invoice_subfolder")
            product_file = product_dir / "Product Details.xlsx"

            po_count = self._count_files(po_dir, (".pdf",))
            export_count = self._count_files(invoice_dir, (".xlsx", ".xls"))
            has_product = product_file.exists()
            mem_count = 0

            if mapping_memory is not None:
                try:
                    mem_count = int(mapping_memory.stats(customer).get("mappings", 0))
                except Exception:
                    mem_count = 0

            total_po += po_count
            total_export += export_count
            memory_total += mem_count

            if not has_product:
                status = "ยังไม่มี Product Details — ควรนำเข้าไฟล์ mapping ก่อน OCR"
                status_color = "#ff7b72"
                missing_mapping += 1
            elif po_count <= 0:
                status = "ยังไม่มีไฟล์ PO รอประมวลผล"
                status_color = "#c9d1d9"
            elif export_count <= 0:
                status = "มี PO แล้ว — ไปแท็บประมวลผล PO เพื่อ OCR/Export"
                status_color = "#f2cc60"
            else:
                status = "พร้อมใช้งาน / มีประวัติ Export แล้ว"
                status_color = "#56d364"
                ready_customers += 1

            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, self._item(customer))
            self.table.setItem(r, 1, self._item("มี" if has_product else "ไม่มี", color="#56d364" if has_product else "#ff7b72"))
            self.table.setItem(r, 2, self._item(po_count))
            self.table.setItem(r, 3, self._item(export_count))
            self.table.setItem(r, 4, self._item(mem_count))
            self.table.setItem(r, 5, self._item(status, color=status_color))

        self.lbl_summary.setText(
            f"ลูกค้าทั้งหมด {len(customers)} ราย | "
            f"ไฟล์ PO {total_po} ไฟล์ | "
            f"Excel Export {total_export} ไฟล์ | "
            f"Mapping Memory {memory_total} รายการ | "
            f"ขาด Product Details {missing_mapping} ราย | "
            f"พร้อมใช้งาน {ready_customers} ราย"
        )

    def open_root_folder(self) -> None:
        folder = Path(str(self.ctx.cfg.get("root_folder", "")))
        if not folder.exists():
            QMessageBox.warning(self, "เปิดโฟลเดอร์หลัก", f"ไม่พบโฟลเดอร์:\n{folder}")
            return
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except Exception as exc:
            QMessageBox.warning(self, "เปิดโฟลเดอร์หลัก", str(exc))
