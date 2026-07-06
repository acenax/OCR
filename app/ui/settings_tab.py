"""Tab 3: settings (Tesseract/poppler paths, folders, OCR + matching params)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QFormLayout, QLineEdit, QPushButton, QHBoxLayout, QSpinBox,
    QFileDialog, QMessageBox, QLabel, QVBoxLayout,
)

from ..context import AppContext


class SettingsTab(QWidget):
    def __init__(self, ctx: AppContext, on_changed=None):
        super().__init__()
        self.ctx = ctx
        self.on_changed = on_changed
        self._build()

    def _path_row(self, initial: str, pick_dir=False, file_filter="") -> tuple[QLineEdit, QHBoxLayout]:
        ed = QLineEdit(initial)
        btn = QPushButton("…")
        btn.setFixedWidth(36)

        def pick():
            if pick_dir:
                p = QFileDialog.getExistingDirectory(self, "เลือกโฟลเดอร์", ed.text())
            else:
                p, _ = QFileDialog.getOpenFileName(self, "เลือกไฟล์", ed.text(), file_filter)
            if p:
                ed.setText(p)
        btn.clicked.connect(pick)
        h = QHBoxLayout()
        h.addWidget(ed); h.addWidget(btn)
        return ed, h

    def _build(self):
        outer = QVBoxLayout(self)
        form = QFormLayout()
        c = self.ctx.cfg
        self.ed_tess, r1 = self._path_row(c["tesseract_path"], file_filter="tesseract.exe (*.exe)")
        self.ed_popp, r2 = self._path_row(c["poppler_path"], pick_dir=True)
        self.ed_root, r3 = self._path_row(c["root_folder"], pick_dir=True)
        self.ed_wh, r4 = self._path_row(c["warehouse_file"], file_filter="Excel (*.xlsx)")
        form.addRow("Tesseract (tesseract.exe):", self._wrap(r1))
        form.addRow("Poppler (โฟลเดอร์ bin):", self._wrap(r2))
        form.addRow("โฟลเดอร์หลัก (ลูกค้าอยู่ข้างใน):", self._wrap(r3))
        form.addRow("ไฟล์คลังสินค้า (stock_group_code):", self._wrap(r4))

        self.sp_dpi = QSpinBox(); self.sp_dpi.setRange(150, 600); self.sp_dpi.setValue(c["dpi"])
        form.addRow("ความละเอียด OCR (DPI):", self.sp_dpi)
        self.sp_fuzzy = QSpinBox(); self.sp_fuzzy.setRange(50, 100); self.sp_fuzzy.setValue(c["fuzzy_threshold"])
        form.addRow("เกณฑ์จับคู่ชื่อสินค้า (fuzzy %):", self.sp_fuzzy)
        outer.addLayout(form)

        row = QHBoxLayout()
        b_save = QPushButton("บันทึกการตั้งค่า")
        b_save.clicked.connect(self.save)
        b_test = QPushButton("ทดสอบ Tesseract")
        b_test.clicked.connect(self.test_tess)
        row.addWidget(b_save); row.addWidget(b_test); row.addStretch()
        outer.addLayout(row)

        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        outer.addWidget(self.lbl)
        outer.addStretch()

    @staticmethod
    def _wrap(layout):
        w = QWidget(); w.setLayout(layout); return w

    def save(self):
        c = self.ctx.cfg
        c.set("tesseract_path", self.ed_tess.text().strip())
        c.set("poppler_path", self.ed_popp.text().strip())
        c.set("root_folder", self.ed_root.text().strip())
        c.set("warehouse_file", self.ed_wh.text().strip())
        c.set("dpi", self.sp_dpi.value())
        c.set("fuzzy_threshold", self.sp_fuzzy.value())
        c.save()
        self.ctx.apply_tesseract()
        self.ctx.reload_warehouse()
        self.lbl.setText("✔ บันทึกการตั้งค่าแล้ว")
        if self.on_changed:
            self.on_changed()

    def test_tess(self):
        import shutil, subprocess
        path = self.ed_tess.text().strip()
        exe = path if shutil.which(path) or path.endswith(".exe") else "tesseract"
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15)
            self.lbl.setText("✔ " + (out.stdout or out.stderr).splitlines()[0])
        except Exception as e:
            QMessageBox.critical(self, "Tesseract", f"เรียกใช้ไม่สำเร็จ:\n{e}")


# === PHASE7 AUTO PATH PATCH ===
# Adds an auto-detect button to Settings without disturbing the original UI.
try:
    from ..auto_paths import find_tesseract as _phase7_find_tesseract, find_poppler as _phase7_find_poppler, diagnostic as _phase7_diagnostic
    _phase7_original_build = SettingsTab._build

    def _phase7_auto_detect_paths(self):
        from pathlib import Path
        starts = []
        try:
            if self.ed_root.text().strip():
                starts.extend([self.ed_root.text().strip(), str(Path(self.ed_root.text().strip()).parent)])
        except Exception:
            pass
        tess = _phase7_find_tesseract(self.ed_tess.text().strip(), start_dirs=starts)
        popp = _phase7_find_poppler(self.ed_popp.text().strip(), start_dirs=starts)
        if tess:
            self.ed_tess.setText(tess)
        if popp:
            self.ed_popp.setText(popp)
        c = self.ctx.cfg
        if tess:
            c.set("tesseract_path", tess)
        if popp or not self.ed_popp.text().strip():
            c.set("poppler_path", popp)
        c.save()
        try:
            self.ctx.apply_tesseract()
        except Exception:
            pass
        self.lbl.setText(
            "✔ ค้นหา Path อัตโนมัติแล้ว\n"
            f"Tesseract: {tess or 'ไม่พบ'}\n"
            f"Poppler: {popp or 'พบใน PATH หรือไม่พบ'}"
        )
        if self.on_changed:
            self.on_changed()

    def _phase7_show_auto_diagnostic(self):
        try:
            msg = _phase7_diagnostic(self.ed_tess.text().strip(), self.ed_popp.text().strip(), [self.ed_root.text().strip()])
            QMessageBox.information(self, "Auto Path Diagnostic", msg)
        except Exception as e:
            QMessageBox.warning(self, "Auto Path Diagnostic", str(e))

    def _phase7_build(self):
        _phase7_original_build(self)
        try:
            row = QHBoxLayout()
            b_auto = QPushButton("🔎 ค้นหา Path อัตโนมัติ")
            b_auto.clicked.connect(self.auto_detect_paths)
            b_diag = QPushButton("ตรวจ Path")
            b_diag.clicked.connect(self.show_auto_diagnostic)
            row.addWidget(b_auto)
            row.addWidget(b_diag)
            row.addStretch()
            self.layout().insertLayout(max(0, self.layout().count() - 2), row)
        except Exception:
            pass

    SettingsTab.auto_detect_paths = _phase7_auto_detect_paths
    SettingsTab.show_auto_diagnostic = _phase7_show_auto_diagnostic
    SettingsTab._build = _phase7_build
except Exception:
    pass
# === END PHASE7 AUTO PATH PATCH ===
