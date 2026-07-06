"""Tab 2: monthly summary stored in the program, with date-range filter + delete."""
from __future__ import annotations

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QDateEdit, QMessageBox, QAbstractItemView,
    QHeaderView, QCheckBox,
)

from ..context import AppContext

COLS = ["ลูกค้า", "เลขที่ PO", "วันที่", "เดือน", "จำนวนรายการ",
        "รวมราคาสินค้า", "VAT", "รวมทั้งสิ้น", "ไฟล์ Excel"]


class SummaryTab(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._build()
        self.refresh()

    def _build(self):
        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("ลูกค้า:"))
        self.cmb_customer = QComboBox()
        self.cmb_customer.addItem("(ทั้งหมด)")
        bar.addWidget(self.cmb_customer)

        self.chk_range = QCheckBox("กรองช่วงวันที่")
        bar.addWidget(self.chk_range)
        bar.addWidget(QLabel("จาก"))
        self.d_from = QDateEdit(QDate.currentDate().addMonths(-1))
        self.d_from.setCalendarPopup(True); self.d_from.setDisplayFormat("yyyy-MM-dd")
        bar.addWidget(self.d_from)
        bar.addWidget(QLabel("ถึง"))
        self.d_to = QDateEdit(QDate.currentDate())
        self.d_to.setCalendarPopup(True); self.d_to.setDisplayFormat("yyyy-MM-dd")
        bar.addWidget(self.d_to)

        b_refresh = QPushButton("ค้นหา / รีเฟรช")
        b_refresh.clicked.connect(self.refresh)
        bar.addWidget(b_refresh)
        b_del = QPushButton("ลบรายการที่เลือก")
        b_del.clicked.connect(self.delete_selected)
        bar.addWidget(b_del)
        bar.addStretch()
        v.addLayout(bar)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(len(COLS) - 1, QHeaderView.Stretch)
        v.addWidget(self.table, 1)

        self.lbl_totals = QLabel("")
        self.lbl_totals.setStyleSheet("font-weight:bold;")
        v.addWidget(self.lbl_totals)

    def refresh(self):
        # keep the customer filter list in sync
        cur = self.cmb_customer.currentText()
        self.cmb_customer.blockSignals(True)
        self.cmb_customer.clear()
        self.cmb_customer.addItem("(ทั้งหมด)")
        self.cmb_customer.addItems(self.ctx.customers())
        idx = self.cmb_customer.findText(cur)
        self.cmb_customer.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_customer.blockSignals(False)

        date_from = date_to = ""
        if self.chk_range.isChecked():
            date_from = self.d_from.date().toString("yyyy-MM-dd")
            date_to = self.d_to.date().toString("yyyy-MM-dd")
        customer = "" if self.cmb_customer.currentIndex() == 0 else self.cmb_customer.currentText()
        rows = self.ctx.store.list_invoices(date_from, date_to, customer)

        self.table.setRowCount(0)
        sum_total = sum_vat = sum_grand = 0.0
        for row in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = [row["customer"], row["po_no"], row["po_date"] or "", row["month"] or "",
                    str(row["item_count"]),
                    f"{row['total']:,.2f}", f"{row['vat']:,.2f}", f"{row['grand_total']:,.2f}",
                    row["excel_path"] or ""]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c in (5, 6, 7):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, it)
            self.table.item(r, 0).setData(Qt.UserRole, row["id"])
            sum_total += row["total"] or 0
            sum_vat += row["vat"] or 0
            sum_grand += row["grand_total"] or 0
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(len(COLS) - 1, QHeaderView.Stretch)
        self.lbl_totals.setText(
            f"รวม {self.table.rowCount()} รายการ   |   รวมราคาสินค้า {sum_total:,.2f}   "
            f"|   VAT {sum_vat:,.2f}   |   รวมทั้งสิ้น {sum_grand:,.2f}")

    def delete_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        if QMessageBox.question(self, "ยืนยันการลบ",
                                f"ต้องการลบ {len(rows)} รายการที่เลือกหรือไม่?") != QMessageBox.Yes:
            return
        for r in rows:
            inv_id = self.table.item(r, 0).data(Qt.UserRole)
            if inv_id is not None:
                self.ctx.store.delete_invoice(int(inv_id))
        self.refresh()

# === PHASE12 CLEANUP DELETE ARABIC PATCH ===
try:
    from PySide6.QtWidgets import QPushButton as _Phase12Button, QMessageBox as _Phase12Msg
    from PySide6.QtCore import Qt as _Phase12Qt
    from ..arabic_digits import to_arabic_digits as _phase12_digits

    def _phase12_summary_delete_selected(self):
        table = getattr(self, "table", None)
        if table is None:
            return
        rows = sorted({i.row() for i in table.selectedIndexes()}, reverse=True)
        if not rows:
            _Phase12Msg.information(self, "ลบสรุปรายเดือน", "กรุณาเลือกรายการที่ต้องการลบก่อน")
            return
        if _Phase12Msg.question(self, "ยืนยันการลบ", f"ต้องการลบสรุปรายเดือน {len(rows)} รายการที่เลือกหรือไม่?") != _Phase12Msg.Yes:
            return
        deleted = 0
        for r in rows:
            inv_id = None
            try:
                inv_id = table.item(r, 0).data(_Phase12Qt.UserRole)
            except Exception:
                pass
            if inv_id is not None:
                self.ctx.store.delete_invoice(int(inv_id)); deleted += 1
            else:
                table.removeRow(r); deleted += 1
        try: self.refresh()
        except Exception: pass
        _Phase12Msg.information(self, "ลบสรุปรายเดือน", f"ลบรายการแล้ว {deleted} รายการ")

    def _phase12_summary_build(self, *_a, **_kw):
        _phase12_old_summary_build(self, *_a, **_kw)
        try:
            if not any("ลบ" in b.text() for b in self.findChildren(_Phase12Button)):
                btn = _Phase12Button("🗑 ลบรายการที่เลือก")
                btn.clicked.connect(self.delete_selected)
                self.layout().insertWidget(0, btn)
        except Exception:
            pass

    def _phase12_summary_refresh(self, *_a, **_kw):
        res = _phase12_old_summary_refresh(self, *_a, **_kw)
        try:
            for r in range(self.table.rowCount()):
                for c in range(self.table.columnCount()):
                    it = self.table.item(r, c)
                    if it:
                        it.setText(_phase12_digits(it.text()))
            if hasattr(self, "lbl_totals"):
                self.lbl_totals.setText(_phase12_digits(self.lbl_totals.text()))
        except Exception:
            pass
        return res

    SummaryTab.delete_selected = _phase12_summary_delete_selected
    if not getattr(SummaryTab, "_phase12_summary_patched", False):
        _phase12_old_summary_build = SummaryTab._build
        _phase12_old_summary_refresh = SummaryTab.refresh
        SummaryTab._build = _phase12_summary_build
        SummaryTab.refresh = _phase12_summary_refresh
        SummaryTab._phase12_summary_patched = True
except Exception:
    pass
