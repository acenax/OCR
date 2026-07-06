"""Manual layout teaching with zoom, status, saved snapshot, full boxes and OCR filters."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QPen, QColor, QBrush, QPixmap, QImage, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsScene,
    QGraphicsView,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QComboBox,
    QMessageBox,
    QButtonGroup,
    QFrame,
)

from .. import ocr, template, config
from ..context import AppContext
try:
    from ..ocr_image_filters import PROFILE_LABELS, make_preview
except Exception:
    PROFILE_LABELS = {"auto": "อัตโนมัติ (แนะนำ)", "raw": "ไม่ปรับภาพ"}
    def make_preview(pil_img, profile="auto", remove_lines=True):
        return pil_img.convert("RGB")


FIELDS = [
    ("item", "ลำดับ", "#888888"),
    ("code", "รหัสสินค้า", "#0d6efd"),
    ("desc", "ชื่อสินค้า/รายการ", "#0a58ca"),
    ("qty", "จำนวน", "#1a7f37"),
    ("price", "ราคา/หน่วย", "#d97706"),
    ("amount", "จำนวนเงิน", "#7c3aed"),
]
FIELD_LABELS = {k: v for k, v, _ in FIELDS}
COLORS = {k: c for k, _, c in FIELDS}
REQUIRED_FIELDS = {"desc", "qty", "price", "amount"}


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", str(name).strip()) or "customer"


def pil_to_pixmap(pil_img) -> QPixmap:
    im = pil_img.convert("RGB")
    data = im.tobytes("raw", "RGB")
    qimg = QImage(data, im.width, im.height, im.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


class BoxCanvas(QGraphicsView):
    """Image view where the user drags a rectangle for the active field."""

    def __init__(self, pixmap: QPixmap):
        self._scene = QGraphicsScene()
        super().__init__(self._scene)
        self.pix_item = self._scene.addPixmap(pixmap)
        self.pw, self.ph = pixmap.width(), pixmap.height()
        self._scene.setSceneRect(0, 0, self.pw, self.ph)

        self.active_field: str | None = None
        self.rects: dict[str, tuple] = {}
        self._start = None
        self._temp = None
        self._zoom = 1.0
        self.on_changed = None

        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)

    def set_field(self, field: str):
        self.active_field = field

    def set_pixmap(self, pixmap: QPixmap):
        old_rects = {k: v[2] for k, v in self.rects.items()}
        self._scene.clear()
        self.pix_item = self._scene.addPixmap(pixmap)
        self.pw, self.ph = pixmap.width(), pixmap.height()
        self._scene.setSceneRect(0, 0, self.pw, self.ph)
        self.rects.clear()
        for k, rect in old_rects.items():
            self._commit(k, rect)
        self.fit()

    def _pen(self, field, width=3):
        p = QPen(QColor(COLORS.get(field, "#d00")))
        p.setWidth(width)
        return p

    def _event_pos(self, e):
        return e.position().toPoint() if hasattr(e, "position") else e.pos()

    def mousePressEvent(self, e):
        if self.active_field and e.button() == Qt.LeftButton and not (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.setDragMode(QGraphicsView.NoDrag)
            self._start = self.mapToScene(self._event_pos(e))
            if self._temp:
                self._scene.removeItem(self._temp)
            self._temp = self._scene.addRect(QRectF(self._start, self._start), self._pen(self.active_field, 2))
        else:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._start and self._temp:
            p = self.mapToScene(self._event_pos(e))
            self._temp.setRect(QRectF(self._start, p).normalized())
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._start and self._temp:
            p = self.mapToScene(self._event_pos(e))
            rect = QRectF(self._start, p).normalized().intersected(QRectF(0, 0, self.pw, self.ph))
            self._scene.removeItem(self._temp)
            self._temp = None
            self._start = None
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            if rect.width() > 8 and rect.height() > 8:
                self._commit(self.active_field, rect)
        else:
            super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if e.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            e.accept()
            return
        super().wheelEvent(e)

    def _commit(self, field, rect: QRectF):
        if not field:
            return
        if field in self.rects:
            old_r, old_l, _ = self.rects[field]
            self._scene.removeItem(old_r)
            self._scene.removeItem(old_l)

        item = self._scene.addRect(rect, self._pen(field, 3), QBrush(QColor(COLORS[field] + "33")))
        label = self._scene.addSimpleText(FIELD_LABELS[field])
        label.setBrush(QBrush(QColor(COLORS[field])))
        label.setPos(rect.left(), max(0, rect.top() - 24))
        self.rects[field] = (item, label, rect)

        if callable(self.on_changed):
            self.on_changed()

    def clear_boxes(self):
        for _, (r, l, _) in list(self.rects.items()):
            self._scene.removeItem(r)
            self._scene.removeItem(l)
        self.rects.clear()
        if callable(self.on_changed):
            self.on_changed()

    def fractions(self) -> dict:
        return {f: (rect.left() / self.pw, rect.right() / self.pw) for f, (_, _, rect) in self.rects.items()}

    def boxes(self) -> dict:
        return {
            f: [
                round(rect.left() / self.pw, 5),
                round(rect.top() / self.ph, 5),
                round(rect.right() / self.pw, 5),
                round(rect.bottom() / self.ph, 5),
            ]
            for f, (_, _, rect) in self.rects.items()
        }

    def top_fraction(self) -> float:
        if not self.rects:
            return 0.0
        return min(rect.top() / self.ph for _, _, rect in self.rects.values())

    def bottom_fraction(self) -> float:
        if not self.rects:
            return 0.97
        return max(rect.bottom() / self.ph for _, _, rect in self.rects.values())

    def fit(self):
        self.resetTransform()
        self.fitInView(self.pix_item, Qt.KeepAspectRatio)
        self._zoom = 1.0

    def zoom_in(self):
        self.scale(1.18, 1.18)
        self._zoom *= 1.18

    def zoom_out(self):
        self.scale(1 / 1.18, 1 / 1.18)
        self._zoom /= 1.18

    def zoom_100(self):
        self.resetTransform()
        self._zoom = 1.0

    def save_snapshot(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        img = QImage(QSize(self.pw, self.ph), QImage.Format_ARGB32)
        img.fill(Qt.GlobalColor.white)
        painter = QPainter(img)
        self._scene.render(painter, QRectF(0, 0, self.pw, self.ph), QRectF(0, 0, self.pw, self.ph))
        painter.end()
        img.save(str(path), "PNG")


class LayoutTeacherDialog(QDialog):
    def __init__(self, ctx: AppContext, customer: str, pdf_path: str, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.customer = customer
        self.pdf_path = pdf_path
        self.setWindowTitle(f"สอนตำแหน่งคอลัมน์ — {customer}")
        self.resize(1280, 920)

        images = ocr.render_pdf(pdf_path, min(260, int(ctx.cfg.get("dpi", 300))), ctx.cfg.get("poppler_path", ""))
        if not images:
            raise RuntimeError("ไม่สามารถแปลง PDF เป็นภาพสำหรับสอนตำแหน่งได้")
        self.pil_original = images[0].convert("RGB")

        self.filter_profile = "auto"
        self.remove_lines = True
        self.show_filtered_preview = False
        self.current_preview = self.pil_original

        self.canvas = BoxCanvas(pil_to_pixmap(self.pil_original))
        self.canvas.on_changed = self.update_status

        root = QVBoxLayout(self)

        tip = QLabel(
            "วิธีใช้: เลือกช่องที่จะสอน แล้วลากกรอบครอบเฉพาะพื้นที่รายการสินค้าในคอลัมน์นั้น "
            "อย่าลากลงไปถึง TOTAL / VAT / GRAND TOTAL เพราะระบบจะอ่านยอดรวมเป็นสินค้า | "
            "ถ้าบิลจาง/สีเพี้ยน ให้เลือกตัวกรองภาพก่อนบันทึกโปรไฟล์"
        )
        tip.setWordWrap(True)
        root.addWidget(tip)

        tool = QHBoxLayout()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for key, label, color in FIELDS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setStyleSheet(f"QPushButton:checked{{background:{color};color:white;}}")
            b.clicked.connect(lambda _=False, k=key: self.canvas.set_field(k))
            self.group.addButton(b)
            tool.addWidget(b)

        tool.addSpacing(16)
        b_zoom_in = QPushButton("+ ซูมเข้า")
        b_zoom_out = QPushButton("− ซูมออก")
        b_fit = QPushButton("พอดีจอ")
        b_100 = QPushButton("100%")
        b_zoom_in.clicked.connect(self.canvas.zoom_in)
        b_zoom_out.clicked.connect(self.canvas.zoom_out)
        b_fit.clicked.connect(self.canvas.fit)
        b_100.clicked.connect(self.canvas.zoom_100)
        tool.addWidget(b_zoom_in)
        tool.addWidget(b_zoom_out)
        tool.addWidget(b_fit)
        tool.addWidget(b_100)
        tool.addStretch()
        root.addLayout(tool)

        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("ตัวกรองภาพ OCR:"))
        self.cmb_filter = QComboBox()
        for key, label in PROFILE_LABELS.items():
            self.cmb_filter.addItem(label, key)
        self.cmb_filter.setCurrentIndex(max(0, self.cmb_filter.findData("auto")))
        self.cmb_filter.currentIndexChanged.connect(self.update_preview_filter)
        filter_bar.addWidget(self.cmb_filter)

        self.chk_remove_lines = QCheckBox("ลบเส้นสีรบกวน")
        self.chk_remove_lines.setChecked(True)
        self.chk_remove_lines.toggled.connect(self.update_preview_filter)
        filter_bar.addWidget(self.chk_remove_lines)

        self.chk_preview_filter = QCheckBox("แสดงภาพหลังปรับ Filter")
        self.chk_preview_filter.toggled.connect(self.update_preview_filter)
        filter_bar.addWidget(self.chk_preview_filter)

        self.cmb_num = QComboBox()
        self.cmb_num.addItem("ทศนิยม 2 ตำแหน่ง (เหมาะกับบิลสแกนจาง)", "fixed2")
        self.cmb_num.addItem("ทศนิยมปกติ", "decimal")
        self.cmb_num.addItem("ทศนิยม 3 ตำแหน่ง", "fixed3")
        filter_bar.addWidget(QLabel("รูปแบบตัวเลข:"))
        filter_bar.addWidget(self.cmb_num)
        filter_bar.addStretch()
        root.addLayout(filter_bar)

        status_box = QFrame()
        status_box.setFrameShape(QFrame.StyledPanel)
        status_layout = QGridLayout(status_box)
        self.status_labels: dict[str, QLabel] = {}
        for idx, (key, label, color) in enumerate(FIELDS):
            lbl = QLabel()
            lbl.setTextFormat(Qt.RichText)
            lbl.setMinimumWidth(150)
            self.status_labels[key] = lbl
            status_layout.addWidget(lbl, idx // 3, idx % 3)
        self.status_summary = QLabel()
        self.status_summary.setStyleSheet("font-weight:bold;color:#f0ad4e;")
        status_layout.addWidget(self.status_summary, 2, 0, 1, 3)
        root.addWidget(status_box)

        root.addWidget(self.canvas, 1)

        opt = QHBoxLayout()
        self.chk_name_below = QCheckBox("ชื่อสินค้าอยู่บรรทัดถัดไป")
        opt.addWidget(self.chk_name_below)
        opt.addStretch()
        b_clear = QPushButton("ล้างกรอบทั้งหมด")
        b_clear.clicked.connect(self.canvas.clear_boxes)
        b_save = QPushButton("💾 บันทึกโปรไฟล์")
        b_save.clicked.connect(self.save)
        b_close = QPushButton("ปิด")
        b_close.clicked.connect(self.reject)
        opt.addWidget(b_clear)
        opt.addWidget(b_save)
        opt.addWidget(b_close)
        root.addLayout(opt)

        buttons = self.group.buttons()
        if len(buttons) >= 3:
            buttons[2].setChecked(True)
        self.canvas.set_field("desc")
        self.update_status()

    def update_preview_filter(self):
        self.filter_profile = self.cmb_filter.currentData() or "auto"
        self.remove_lines = bool(self.chk_remove_lines.isChecked())
        self.show_filtered_preview = bool(self.chk_preview_filter.isChecked())
        if self.show_filtered_preview:
            self.current_preview = make_preview(self.pil_original, self.filter_profile, self.remove_lines)
        else:
            self.current_preview = self.pil_original
        self.canvas.set_pixmap(pil_to_pixmap(self.current_preview))
        self.update_status()

    def showEvent(self, e):
        super().showEvent(e)
        self.canvas.fit()

    def update_status(self):
        have = set(self.canvas.rects.keys())
        for key, label, color in FIELDS:
            if key in have:
                self.status_labels[key].setText(f"<span style='color:#21c55d'>✓ {label}: สอนแล้ว</span>")
            else:
                mark = "จำเป็น" if key in REQUIRED_FIELDS else "เสริม"
                self.status_labels[key].setText(f"<span style='color:#ff6b6b'>✗ {label}: ยังไม่สอน ({mark})</span>")

        missing_required = REQUIRED_FIELDS - have
        filter_label = self.cmb_filter.currentText() if hasattr(self, "cmb_filter") else "อัตโนมัติ"
        if missing_required:
            names = ", ".join(FIELD_LABELS[k] for k in missing_required)
            self.status_summary.setText(f"สถานะ: ยังไม่พร้อม ต้องสอนเพิ่ม: {names} | Filter: {filter_label}")
            self.status_summary.setStyleSheet("font-weight:bold;color:#ff6b6b;")
        else:
            self.status_summary.setText(f"สถานะ: พร้อมบันทึกโปรไฟล์ | Filter: {filter_label} | ล้าง Cache แล้ว OCR ใหม่")
            self.status_summary.setStyleSheet("font-weight:bold;color:#21c55d;")

    def save(self):
        cols = self.canvas.fractions()
        boxes = self.canvas.boxes()
        missing = REQUIRED_FIELDS - set(cols)
        if missing:
            names = ", ".join(FIELD_LABELS[m] for m in missing)
            QMessageBox.warning(self, "ยังไม่ครบ", "กรุณาลากกรอบอย่างน้อย: " + names)
            return

        snapshot_dir = config.TEMPLATE_DIR / "_teaching_snapshots"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_name = f"{_safe_name(self.customer)}_{stamp}.png"
        snapshot_path = snapshot_dir / snapshot_name
        self.canvas.save_snapshot(snapshot_path)

        filter_snapshot = ""
        if self.show_filtered_preview:
            filter_path = snapshot_dir / f"{_safe_name(self.customer)}_{stamp}_filter_{self.filter_profile}.png"
            filter_path.parent.mkdir(parents=True, exist_ok=True)
            self.current_preview.save(filter_path)
            filter_snapshot = str(filter_path)

        number_mode = self.cmb_num.currentData() if hasattr(self, "cmb_num") else "fixed2"
        profile = {
            "customer": self.customer,
            "mode": "anchors",
            "number_mode": number_mode or "fixed2",
            "name_below": self.chk_name_below.isChecked(),
            "data_top_frac": max(0.0, round(self.canvas.top_fraction() - 0.003, 5)),
            "bottom_frac": min(0.99, round(self.canvas.bottom_fraction() + 0.003, 5)),
            "columns": {k: [round(lo, 5), round(hi, 5)] for k, (lo, hi) in cols.items()},
            "boxes": boxes,
            "ocr_filter_profile": self.filter_profile,
            "ocr_remove_colored_lines": self.remove_lines,
            "ocr_show_filtered_preview": self.show_filtered_preview,
            "trained_at": datetime.now().isoformat(timespec="seconds"),
            "trained_from_pdf": str(self.pdf_path),
            "trained_snapshot": str(snapshot_path),
            "trained_filter_snapshot": filter_snapshot,
            "ocr_hint": "ใช้กรอบสินค้า + filter ภาพก่อน OCR; ถ้าข้อมูลยังเพี้ยน ให้ลอง filter strong/line_clean แล้วล้าง Cache",
        }

        template.save_profile(self.customer, profile)

        QMessageBox.information(
            self,
            "บันทึกแล้ว",
            f"บันทึกตำแหน่งคอลัมน์ของ '{self.customer}' เรียบร้อย\n\n"
            f"Filter: {self.cmb_filter.currentText()}\n"
            f"เก็บภาพที่สอนตำแหน่งไว้ที่:\n{snapshot_path}\n\n"
            "ให้กด 'ล้าง OCR Cache ทั้งหมด' หรือ 'ล้าง OCR Cache ของไฟล์ที่เลือก' แล้ว OCR ใหม่",
        )
        self.accept()
