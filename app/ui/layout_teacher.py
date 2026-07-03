"""Manual layout teaching: the user draws a box around each column on the PO image.

Solves the "every customer's PO looks different" problem without hand-editing JSON:
pick a field (ชื่อสินค้า / จำนวน / ราคา / ...), drag a rectangle over that column,
then Save -> writes templates/<customer>.json (mode=anchors, explicit column boxes).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPen, QColor, QBrush, QPixmap, QImage
from PySide6.QtWidgets import (
    QDialog, QGraphicsScene, QGraphicsView, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QCheckBox, QMessageBox, QButtonGroup, QSpinBox, QWidget,
)

from .. import ocr, template
from ..context import AppContext

# field key -> (Thai label, colour)
FIELDS = [
    ("item", "ลำดับ", "#888888"),
    ("desc", "ชื่อสินค้า/รายการ", "#0a58ca"),
    ("qty", "จำนวน", "#1a7f37"),
    ("price", "ราคา/หน่วย", "#d97706"),
    ("amount", "จำนวนเงิน", "#7c3aed"),
]
COLORS = {k: c for k, _, c in FIELDS}


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
        self.active_field = None
        self.rects: dict[str, tuple] = {}     # field -> (rect_item, label_item, QRectF)
        self._start = None
        self._temp = None
        self.setRenderHints(self.renderHints())
        self.setDragMode(QGraphicsView.NoDrag)

    def set_field(self, field: str):
        self.active_field = field

    def _pen(self, field, width=3):
        p = QPen(QColor(COLORS.get(field, "#d00")))
        p.setWidth(width)
        return p

    def mousePressEvent(self, e):
        if self.active_field and e.button() == Qt.LeftButton:
            self._start = self.mapToScene(e.pos())
            if self._temp:
                self._scene.removeItem(self._temp)
            self._temp = self._scene.addRect(QRectF(self._start, self._start),
                                             self._pen(self.active_field, 2))
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._start and self._temp:
            self._temp.setRect(QRectF(self._start, self.mapToScene(e.pos())).normalized())
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._start and self._temp:
            rect = self._temp.rect().intersected(QRectF(0, 0, self.pw, self.ph))
            self._scene.removeItem(self._temp)
            self._temp = None
            self._start = None
            if rect.width() > 5 and rect.height() > 3:
                self._commit(self.active_field, rect)
        else:
            super().mouseReleaseEvent(e)

    def _commit(self, field, rect: QRectF):
        # replace any previous box for this field
        if field in self.rects:
            old_r, old_l, _ = self.rects[field]
            self._scene.removeItem(old_r)
            self._scene.removeItem(old_l)
        item = self._scene.addRect(rect, self._pen(field, 3),
                                   QBrush(QColor(COLORS[field] + "22")))
        label = self._scene.addSimpleText(dict((k, v) for k, v, _ in FIELDS)[field])
        label.setBrush(QBrush(QColor(COLORS[field])))
        label.setPos(rect.left(), max(0, rect.top() - 20))
        self.rects[field] = (item, label, rect)

    def clear_boxes(self):
        for _, (r, l, _) in self.rects.items():
            self._scene.removeItem(r)
            self._scene.removeItem(l)
        self.rects.clear()

    def fractions(self) -> dict:
        return {f: (rect.left() / self.pw, rect.right() / self.pw)
                for f, (_, _, rect) in self.rects.items()}

    def top_fraction(self) -> float:
        if not self.rects:
            return 0.0
        return min(rect.top() / self.ph for _, _, rect in self.rects.values())

    def fit(self):
        self.fitInView(self.pix_item, Qt.KeepAspectRatio)


class LayoutTeacherDialog(QDialog):
    def __init__(self, ctx: AppContext, customer: str, pdf_path: str, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.customer = customer
        self.setWindowTitle(f"สอนตำแหน่งคอลัมน์ — {customer}")
        self.resize(1150, 850)

        # render page 1 for teaching (columns are the same on every page)
        images = ocr.render_pdf(pdf_path, min(200, ctx.cfg["dpi"]), ctx.cfg["poppler_path"])
        self.canvas = BoxCanvas(pil_to_pixmap(images[0]))

        root = QVBoxLayout(self)
        tip = QLabel("วิธีใช้: กดปุ่มเลือกช่องที่จะสอน (เช่น ‘ชื่อสินค้า’) แล้ว "
                     "<b>ลากเมาส์ครอบคอลัมน์นั้นบนเอกสาร</b> ทำให้ครบทุกช่อง แล้วกด ‘บันทึกโปรไฟล์’")
        tip.setWordWrap(True)
        root.addWidget(tip)

        bar = QHBoxLayout()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for key, label, color in FIELDS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setStyleSheet(f"QPushButton:checked{{background:{color};color:white;}}")
            b.clicked.connect(lambda _=False, k=key: self.canvas.set_field(k))
            self.group.addButton(b)
            bar.addWidget(b)
        bar.addStretch()
        root.addLayout(bar)

        root.addWidget(self.canvas, 1)

        opt = QHBoxLayout()
        self.chk_name_below = QCheckBox("ชื่อสินค้าอยู่บรรทัดถัดไป")
        opt.addWidget(self.chk_name_below)
        opt.addWidget(QLabel("รูปแบบตัวเลข:"))
        self.cmb_num = QComboBox()
        self.cmb_num.addItem("ทศนิยม 2 ตำแหน่ง (สแกนจาง)", "fixed2")
        self.cmb_num.addItem("ทศนิยม 3 ตำแหน่ง", "fixed3")
        self.cmb_num.addItem("ปกติ (อ่านจุดทศนิยมตรง ๆ)", "decimal")
        opt.addWidget(self.cmb_num)
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

        # preselect the first field
        first = self.group.buttons()[1]  # ชื่อสินค้า
        first.setChecked(True)
        self.canvas.set_field("desc")

    def showEvent(self, e):
        super().showEvent(e)
        self.canvas.fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.canvas.fit()

    def save(self):
        cols = self.canvas.fractions()
        required = {"desc", "price", "amount"}
        missing = required - set(cols)
        if missing:
            names = {k: l for k, l, _ in FIELDS}
            QMessageBox.warning(
                self, "ยังไม่ครบ",
                "กรุณาลากกรอบอย่างน้อย: " + ", ".join(names[m] for m in
                                                       ["desc", "price", "amount"] if m in missing))
            return
        profile = {
            "customer": self.customer,
            "mode": "anchors",
            "number_mode": self.cmb_num.currentData(),
            "name_below": self.chk_name_below.isChecked(),
            "data_top_frac": max(0.0, round(self.canvas.top_fraction() - 0.01, 4)),
            "bottom_frac": 0.97,
            "columns": {k: [round(lo, 4), round(hi, 4)] for k, (lo, hi) in cols.items()},
        }
        template.save_profile(self.customer, profile)
        QMessageBox.information(
            self, "บันทึกแล้ว",
            f"บันทึกตำแหน่งคอลัมน์ของ '{self.customer}' เรียบร้อย\n"
            f"กด ‘อ่านเอกสาร (OCR)’ อีกครั้งเพื่อใช้ตำแหน่งใหม่")
        self.accept()
