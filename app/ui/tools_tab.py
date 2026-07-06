"""Data tools tab: Product Details validation, mapping memory, and DB backup."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QMessageBox, QFileDialog, QGroupBox,
    QTabWidget, QHeaderView, QAbstractItemView
)

from ..context import AppContext
from .. import pipeline, product_validator, backup_tools, mapping_memory


class ToolsTab(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._build()
        self.refresh_customers()
        self.refresh_backups()

    def _build(self):
        root = QVBoxLayout(self)
        title = QLabel("เครื่องมือข้อมูล / ตรวจคุณภาพ / สำรองข้อมูล")
        title.setStyleSheet("font-size:18px;font-weight:700;")
        root.addWidget(title)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_product_tab(), "ตรวจ Product Details")
        self.tabs.addTab(self._build_memory_tab(), "Mapping Memory")
        self.tabs.addTab(self._build_backup_tab(), "Backup / Restore")
        root.addWidget(self.tabs, 1)

    # ---------------- Product Details Validator ----------------
    def _build_product_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        row = QHBoxLayout()
        row.addWidget(QLabel("ลูกค้า:"))
        self.cmb_product_customer = QComboBox()
        row.addWidget(self.cmb_product_customer, 1)
        btn = QPushButton("ตรวจสอบไฟล์ Product Details")
        btn.clicked.connect(self.validate_product_details)
        row.addWidget(btn)
        lay.addLayout(row)

        self.lbl_product_summary = QLabel("เลือกชื่อลูกค้าแล้วกดตรวจสอบ")
        self.lbl_product_summary.setWordWrap(True)
        lay.addWidget(self.lbl_product_summary)

        self.tbl_product = QTableWidget(0, 5)
        self.tbl_product.setHorizontalHeaderLabels(["ระดับ", "แถว", "คอลัมน์", "รายละเอียด", "ค่า"])
        self.tbl_product.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tbl_product.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl_product.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self.tbl_product, 1)
        return w

    def validate_product_details(self):
        customer = self.cmb_product_customer.currentText().strip()
        if not customer:
            QMessageBox.warning(self, "ตรวจ Product Details", "กรุณาเลือกลูกค้าก่อน")
            return
        path = pipeline.product_file_for(
            self.ctx.cfg["root_folder"], customer, self.ctx.cfg["product_subfolder"]
        )
        if not path:
            path = str(Path(self.ctx.cfg["root_folder"]) / customer / self.ctx.cfg["product_subfolder"] / "Product Details.xlsx")
        summary, issues = product_validator.validate_product_details(path)
        self.tbl_product.setRowCount(0)
        for issue in issues:
            self._add_product_issue(issue)

        if summary.get("critical", 0):
            status = "❌ ต้องแก้ก่อนใช้งานจริง"
            color = "#b02a37"
        elif summary.get("review", 0):
            status = "⚠️ ใช้งานได้แต่ควรตรวจทาน"
            color = "#b36b00"
        else:
            status = "✅ ไม่พบปัญหาสำคัญ"
            color = "#1a7f37"
        self.lbl_product_summary.setStyleSheet(f"color:{color};font-weight:700;")
        self.lbl_product_summary.setText(
            f"{status}\n"
            f"ไฟล์: {summary.get('path', '')}\n"
            f"แถวข้อมูล: {summary.get('rows', 0)} | "
            f"Critical: {summary.get('critical', 0)} | Review: {summary.get('review', 0)}\n"
            f"tmc column: {summary.get('tmc_col', '-') or '-'} | "
            f"name columns: {', '.join(summary.get('name_cols', []) or []) or '-'}"
        )

    def _add_product_issue(self, issue):
        r = self.tbl_product.rowCount()
        self.tbl_product.insertRow(r)
        values = [issue.severity, str(issue.row), issue.column, issue.message, issue.value]
        for c, v in enumerate(values):
            it = QTableWidgetItem(v)
            if issue.severity == "critical":
                it.setBackground(Qt.GlobalColor.red)
                it.setForeground(Qt.GlobalColor.white)
            elif issue.severity == "review":
                it.setBackground(Qt.GlobalColor.yellow)
            self.tbl_product.setItem(r, c, it)

    # ---------------- Mapping Memory Manager ----------------
    def _build_memory_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("ลูกค้า:"))
        self.cmb_memory_customer = QComboBox()
        row.addWidget(self.cmb_memory_customer, 1)
        btn_load = QPushButton("โหลด Mapping")
        btn_load.clicked.connect(self.load_mapping_memory)
        btn_delete = QPushButton("ลบ Mapping ที่เลือก")
        btn_delete.clicked.connect(self.delete_selected_mapping)
        btn_export = QPushButton("Export mapping_memory.json")
        btn_export.clicked.connect(self.export_mapping_memory)
        row.addWidget(btn_load)
        row.addWidget(btn_delete)
        row.addWidget(btn_export)
        lay.addLayout(row)

        self.lbl_memory_summary = QLabel("Mapping Memory ช่วยจำ tmc_code/stock_group_code ที่เคยแก้แล้ว")
        self.lbl_memory_summary.setWordWrap(True)
        lay.addWidget(self.lbl_memory_summary)

        self.tbl_memory = QTableWidget(0, 6)
        self.tbl_memory.setHorizontalHeaderLabels(["Key", "ข้อความ OCR", "tmc_code", "stock_group_code", "ใช้แล้ว", "แก้ล่าสุด"])
        self.tbl_memory.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_memory.setColumnHidden(0, True)
        self.tbl_memory.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_memory.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.tbl_memory, 1)
        return w

    def _load_memory_all(self) -> dict:
        path = mapping_memory.MEMORY_PATH
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_memory_all(self, data: dict) -> None:
        mapping_memory.MEMORY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_mapping_memory(self):
        customer = self.cmb_memory_customer.currentText().strip()
        data = self._load_memory_all()
        rows = data.get(customer, {}) if customer else {}
        self.tbl_memory.setRowCount(0)
        for key, rec in sorted(rows.items(), key=lambda kv: str(kv[1].get("updated_at", "")), reverse=True):
            r = self.tbl_memory.rowCount()
            self.tbl_memory.insertRow(r)
            vals = [
                key,
                str(rec.get("source_text", "")),
                str(rec.get("tmc_code", "")),
                str(rec.get("stock_group_code", "")),
                str(rec.get("used_count", 0)),
                str(rec.get("updated_at", "")),
            ]
            for c, v in enumerate(vals):
                self.tbl_memory.setItem(r, c, QTableWidgetItem(v))
        self.lbl_memory_summary.setText(f"ลูกค้า {customer or '-'} มี Mapping Memory {len(rows)} รายการ")

    def delete_selected_mapping(self):
        customer = self.cmb_memory_customer.currentText().strip()
        if not customer:
            return
        keys = []
        for idx in self.tbl_memory.selectedIndexes():
            if idx.column() == 0:
                item = self.tbl_memory.item(idx.row(), 0)
                if item:
                    keys.append(item.text())
        if not keys:
            rows = sorted({i.row() for i in self.tbl_memory.selectedIndexes()})
            for r in rows:
                item = self.tbl_memory.item(r, 0)
                if item:
                    keys.append(item.text())
        keys = sorted(set(keys))
        if not keys:
            QMessageBox.information(self, "ลบ Mapping", "กรุณาเลือกรายการที่ต้องการลบ")
            return
        ans = QMessageBox.question(self, "ยืนยันลบ Mapping", f"ต้องการลบ Mapping {len(keys)} รายการของลูกค้า {customer} หรือไม่?")
        if ans != QMessageBox.Yes:
            return
        data = self._load_memory_all()
        cust_map = data.get(customer, {})
        for k in keys:
            cust_map.pop(k, None)
        data[customer] = cust_map
        self._save_memory_all(data)
        self.load_mapping_memory()

    def export_mapping_memory(self):
        src = mapping_memory.MEMORY_PATH
        if not src.exists():
            QMessageBox.information(self, "Export Mapping", "ยังไม่มีไฟล์ mapping_memory.json")
            return
        out, _ = QFileDialog.getSaveFileName(self, "บันทึก mapping_memory.json", str(src.name), "JSON (*.json)")
        if not out:
            return
        Path(out).write_bytes(src.read_bytes())
        QMessageBox.information(self, "Export Mapping", f"Export แล้ว:\n{out}")

    # ---------------- Backup / Restore ----------------
    def _build_backup_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("สำรองฐานข้อมูล SQLite ก่อนแก้ระบบ/ก่อน Restore ทุกครั้ง เพื่อลดความเสี่ยงข้อมูลสรุปรายเดือนหาย")
        info.setWordWrap(True)
        lay.addWidget(info)

        row = QHBoxLayout()
        btn_backup = QPushButton("Backup DB ตอนนี้")
        btn_backup.clicked.connect(self.backup_database)
        btn_restore = QPushButton("Restore จากไฟล์ Backup")
        btn_restore.clicked.connect(self.restore_database)
        btn_refresh = QPushButton("Refresh รายการ Backup")
        btn_refresh.clicked.connect(self.refresh_backups)
        row.addWidget(btn_backup)
        row.addWidget(btn_restore)
        row.addWidget(btn_refresh)
        row.addStretch()
        lay.addLayout(row)

        self.lbl_backup_summary = QLabel("")
        lay.addWidget(self.lbl_backup_summary)
        self.tbl_backup = QTableWidget(0, 3)
        self.tbl_backup.setHorizontalHeaderLabels(["ไฟล์", "ขนาด", "ตำแหน่ง"])
        self.tbl_backup.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_backup.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_backup.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.tbl_backup, 1)
        return w

    def backup_database(self):
        try:
            dest = backup_tools.backup_database("manual")
            QMessageBox.information(self, "Backup DB", f"สำรองข้อมูลเรียบร้อย:\n{dest}")
        except Exception as exc:
            QMessageBox.critical(self, "Backup DB", str(exc))
        self.refresh_backups()

    def restore_database(self):
        file, _ = QFileDialog.getOpenFileName(self, "เลือกไฟล์ DB Backup", str(backup_tools.BACKUP_DIR), "SQLite DB (*.db)")
        if not file:
            return
        ans = QMessageBox.question(
            self,
            "ยืนยัน Restore",
            "การ Restore จะนำฐานข้อมูล backup มาวางทับฐานข้อมูลปัจจุบัน\n"
            "ระบบจะสร้าง safety backup ของฐานข้อมูลปัจจุบันก่อนเสมอ\n\n"
            "ต้องการดำเนินการต่อหรือไม่?",
        )
        if ans != QMessageBox.Yes:
            return
        try:
            safety = backup_tools.restore_database(file)
            QMessageBox.information(self, "Restore DB", f"Restore เรียบร้อย\nSafety backup: {safety}")
        except Exception as exc:
            QMessageBox.critical(self, "Restore DB", str(exc))
        self.refresh_backups()

    def refresh_backups(self):
        if not hasattr(self, "tbl_backup"):
            return
        backups = backup_tools.list_backups()
        self.tbl_backup.setRowCount(0)
        for p in backups:
            r = self.tbl_backup.rowCount()
            self.tbl_backup.insertRow(r)
            size_mb = p.stat().st_size / (1024 * 1024)
            vals = [p.name, f"{size_mb:.2f} MB", str(p)]
            for c, v in enumerate(vals):
                self.tbl_backup.setItem(r, c, QTableWidgetItem(v))
        self.lbl_backup_summary.setText(f"พบไฟล์ Backup {len(backups)} รายการ | โฟลเดอร์: {backup_tools.BACKUP_DIR}")

    # ---------------- shared ----------------
    def refresh_customers(self):
        customers = self.ctx.customers()
        for combo_name in ("cmb_product_customer", "cmb_memory_customer"):
            combo = getattr(self, combo_name, None)
            if combo is None:
                continue
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(customers)
            if current:
                idx = combo.findText(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.blockSignals(False)
