"""OCR queue/status tab for Phase 5."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QAbstractItemView,
)

from ..context import AppContext
from .. import queue_status, ocr_cache, audit_log

COLS = ["สถานะ", "ลูกค้า", "ไฟล์", "รายการ", "Cache", "อัปเดตล่าสุด", "ข้อความ", "Path"]
_STATUS_LABELS = {
    "queued": "รอคิว", "processing": "กำลังอ่าน", "ready": "พร้อม Export",
    "need_review": "ต้องตรวจ", "failed": "ผิดพลาด", "deleted": "ลบแล้ว",
}
_STATUS_COLORS = {
    "queued": "#57606a", "processing": "#0969da", "ready": "#1a7f37",
    "need_review": "#b36b00", "failed": "#b02a37", "deleted": "#57606a",
}


class QueueTab(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._build()
        self.refresh()

    def _build(self):
        root = QVBoxLayout(self)
        title = QLabel("OCR Queue / สถานะงานประมวลผล")
        title.setStyleSheet("font-size:20px;font-weight:700;")
        root.addWidget(title)
        self.lbl_summary = QLabel("-")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setStyleSheet("padding:8px;border:1px solid #d0d7de;border-radius:8px;background:#f6f8fa;")
        root.addWidget(self.lbl_summary)
        row = QHBoxLayout()
        btn_refresh = QPushButton("↻ Refresh")
        btn_refresh.clicked.connect(self.refresh)
        btn_open_cache = QPushButton("เปิดโฟลเดอร์ OCR Cache")
        btn_open_cache.clicked.connect(self.open_cache_dir)
        btn_clear_done = QPushButton("ล้างสถานะ Ready/Deleted")
        btn_clear_done.clicked.connect(self.clear_done)
        btn_clear_failed = QPushButton("ล้างสถานะ Failed")
        btn_clear_failed.clicked.connect(self.clear_failed)
        row.addWidget(btn_refresh); row.addWidget(btn_open_cache); row.addWidget(btn_clear_done); row.addWidget(btn_clear_failed); row.addStretch()
        root.addLayout(row)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        for c in range(len(COLS)):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.Stretch if c in (6, 7) else QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self.open_selected_folder)
        root.addWidget(self.table, 1)
        note = QLabel("ดับเบิลคลิกแถวเพื่อเปิดโฟลเดอร์ไฟล์ PO | สถานะนี้เป็นข้อมูลช่วยติดตาม ไม่ได้ลบ/ย้ายไฟล์จริง")
        note.setWordWrap(True)
        note.setStyleSheet("color:#57606a;")
        root.addWidget(note)

    @staticmethod
    def _item(text: object, *, color: str | None = None) -> QTableWidgetItem:
        it = QTableWidgetItem(str(text))
        if color:
            it.setForeground(QBrush(QColor(color)))
        return it

    def refresh(self):
        records = queue_status.list_statuses()
        self.table.setRowCount(0)
        stats = queue_status.stats()
        cache_stats = ocr_cache.stats()
        summary_parts = []
        for key in ("processing", "need_review", "failed", "ready", "queued", "deleted"):
            if stats.get(key, 0):
                summary_parts.append(f"{_STATUS_LABELS.get(key, key)} {stats[key]}")
        if not summary_parts:
            summary_parts.append("ยังไม่มีประวัติ OCR Queue")
        size_mb = cache_stats.get("size_bytes", 0) / (1024 * 1024)
        self.lbl_summary.setText(" | ".join(summary_parts) + f" | OCR Cache {cache_stats.get('count', 0)} รายการ ({size_mb:.2f} MB)")
        for rec in records:
            status = str(rec.get("status", ""))
            label = _STATUS_LABELS.get(status, status)
            color = _STATUS_COLORS.get(status, "#24292f")
            warnings = rec.get("warnings") or []
            message = str(rec.get("message") or "")
            if warnings and not message:
                message = " | ".join(map(str, warnings[:3]))
            r = self.table.rowCount()
            self.table.insertRow(r)
            values = [label, rec.get("customer", ""), rec.get("name", ""), rec.get("item_count", 0), "ใช้ Cache" if rec.get("cache_hit") else "-", rec.get("updated_at", ""), message, rec.get("path", "")]
            for c, value in enumerate(values):
                self.table.setItem(r, c, self._item(value, color=color if c == 0 else None))

    def open_cache_dir(self):
        folder = Path(ocr_cache.CACHE_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except Exception as exc:
            QMessageBox.warning(self, "เปิด Cache", str(exc))

    def open_selected_folder(self):
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 7)
        if not item:
            return
        path = Path(item.text())
        folder = path.parent if path.suffix else path
        if not folder.exists():
            QMessageBox.warning(self, "เปิดโฟลเดอร์", f"ไม่พบโฟลเดอร์:\n{folder}")
            return
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except Exception as exc:
            QMessageBox.warning(self, "เปิดโฟลเดอร์", str(exc))

    def clear_done(self):
        n = queue_status.clear({"ready", "deleted"})
        audit_log.log_event("queue_clear_done", count=n)
        self.refresh()

    def clear_failed(self):
        n = queue_status.clear({"failed"})
        audit_log.log_event("queue_clear_failed", count=n)
        self.refresh()

# === PHASE12 CLEANUP DELETE ARABIC PATCH ===
try:
    from PySide6.QtWidgets import QPushButton as _Phase12Button, QMessageBox as _Phase12Msg, QTableWidget as _Phase12Table
    from ..arabic_digits import to_arabic_digits as _phase12_digits

    def _phase12_queue_table(self):
        table = getattr(self, "table", None) or getattr(self, "tbl", None)
        if table is None:
            try:
                table = self.findChild(_Phase12Table)
            except Exception:
                table = None
        return table

    def _phase12_queue_delete_selected(self):
        table = _phase12_queue_table(self)
        if table is None:
            _Phase12Msg.warning(self, "ลบ Queue", "ไม่พบตาราง Queue")
            return
        rows = sorted({i.row() for i in table.selectedIndexes()}, reverse=True)
        if not rows:
            _Phase12Msg.information(self, "ลบ Queue", "กรุณาเลือกรายการ Queue ที่ต้องการลบก่อน")
            return
        if _Phase12Msg.question(self, "ยืนยันลบ Queue", f"ต้องการลบรายการ Queue ที่เลือก {len(rows)} รายการหรือไม่?") != _Phase12Msg.Yes:
            return
        for r in rows:
            table.removeRow(r)
        # Try to clear persistent data if the queue module exposes a known clear/remove method.
        try:
            from .. import queue_status as _qs
            for name in ("clear_deleted_rows", "save_current_table", "persist_table"):
                fn = getattr(_qs, name, None)
                if callable(fn):
                    try: fn()
                    except TypeError: fn(self)
                    break
        except Exception:
            pass
        _Phase12Msg.information(self, "ลบ Queue", "ลบรายการ Queue ออกจากหน้าจอแล้ว")

    def _phase12_queue_build(self, *_a, **_kw):
        _phase12_old_queue_build(self, *_a, **_kw)
        try:
            if not getattr(self, "_phase12_queue_delete_btn_added", False):
                btn = _Phase12Button("🗑 ลบรายการ Queue ที่เลือก")
                btn.clicked.connect(self.delete_selected_queue)
                self.layout().addWidget(btn)
                self._phase12_queue_delete_btn_added = True
        except Exception:
            pass

    def _phase12_queue_refresh(self, *_a, **_kw):
        res = _phase12_old_queue_refresh(self, *_a, **_kw)
        try:
            table = _phase12_queue_table(self)
            if table:
                for r in range(table.rowCount()):
                    for c in range(table.columnCount()):
                        it = table.item(r, c)
                        if it:
                            it.setText(_phase12_digits(it.text()))
        except Exception:
            pass
        return res

    # Find QueueTab class in this module by name.
    _Phase12QueueClass = globals().get("QueueTab") or globals().get("OCRQueueTab") or globals().get("OcrQueueTab")
    if _Phase12QueueClass is not None:
        _Phase12QueueClass.delete_selected_queue = _phase12_queue_delete_selected
        if not getattr(_Phase12QueueClass, "_phase12_queue_patched", False):
            if hasattr(_Phase12QueueClass, "_build"):
                _phase12_old_queue_build = _Phase12QueueClass._build
                _Phase12QueueClass._build = _phase12_queue_build
            if hasattr(_Phase12QueueClass, "refresh"):
                _phase12_old_queue_refresh = _Phase12QueueClass.refresh
                _Phase12QueueClass.refresh = _phase12_queue_refresh
            _Phase12QueueClass._phase12_queue_patched = True
except Exception:
    pass

# --- HOTFIX_QUEUE_PERSISTENT_SOFT_DELETE_V1 ---
# Persistent soft delete for OCR Queue rows.
# This patch hides selected queue rows permanently across refresh/clear-status
# without deleting the original PDF or OCR cache files.
def _install_queue_persistent_soft_delete_patch():
    try:
        import json
        from pathlib import Path
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox, QPushButton, QTableWidget, QAbstractItemView
    except Exception:
        return

    cls = globals().get("QueueTab")
    if cls is None or getattr(cls, "_queue_persistent_soft_delete_installed", False):
        return

    def _state_path(self):
        try:
            ctx = getattr(self, "ctx", None) or getattr(self, "context", None) or getattr(self, "app_context", None)
            cfg = getattr(ctx, "config", None) if ctx is not None else None
            root = None
            for attr in ("root_dir", "root_folder", "base_dir", "base_folder"):
                val = getattr(cfg, attr, None) if cfg is not None else None
                if val:
                    root = Path(str(val))
                    break
            if root is None:
                # keep this file next to settings.json / running project root
                root = Path.cwd()
        except Exception:
            root = Path.cwd()
        return root / ".queue_deleted.json"

    def _load_deleted(self):
        path = _state_path(self)
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    rows = data.get("rows", [])
                    return set(str(x) for x in rows if x)
                if isinstance(data, list):
                    return set(str(x) for x in data if x)
        except Exception:
            pass
        return set()

    def _save_deleted(self, keys):
        path = _state_path(self)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"rows": sorted(set(str(x) for x in keys if x))}
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            try:
                QMessageBox.warning(self, "Queue", "บันทึกสถานะรายการที่ลบไม่สำเร็จ:\n" + str(exc))
            except Exception:
                pass

    def _find_table(self):
        try:
            tables = self.findChildren(QTableWidget)
        except Exception:
            tables = []
        if not tables:
            return None
        # Prefer the table that has Path / ไฟล์ / สถานะ headers.
        best = tables[0]
        best_score = -1
        for table in tables:
            score = 0
            for col in range(table.columnCount()):
                item = table.horizontalHeaderItem(col)
                text = item.text().strip().lower() if item else ""
                if text in ("path",) or "path" in text:
                    score += 5
                if "ไฟล์" in text or "สถานะ" in text or "ลูกค้า" in text:
                    score += 2
            if score > best_score:
                best, best_score = table, score
        return best

    def _row_values(table, row):
        vals = []
        for col in range(table.columnCount()):
            item = table.item(row, col)
            if item:
                vals.append(item.text().strip())
        return vals

    def _row_key(table, row):
        vals = _row_values(table, row)
        # Prefer an absolute file path because it survives table refresh best.
        for v in reversed(vals):
            s = str(v).strip().replace("/", "\\")
            if ":\\" in s or s.startswith("\\\\"):
                return "PATH::" + s.lower()
        # Fallback: customer + file columns, or full row signature.
        short = "|".join(vals[:4]) if len(vals) >= 4 else "|".join(vals)
        return "ROW::" + short.lower()

    def _selected_rows(table):
        rows = set()
        try:
            for idx in table.selectionModel().selectedRows():
                rows.add(idx.row())
        except Exception:
            pass
        try:
            for item in table.selectedItems():
                rows.add(item.row())
        except Exception:
            pass
        return sorted(rows)

    def _apply_filter(self):
        table = _find_table(self)
        if table is None:
            return
        deleted = _load_deleted(self)
        # Remove hidden rows from bottom to top.
        for row in range(table.rowCount() - 1, -1, -1):
            try:
                if _row_key(table, row) in deleted:
                    table.removeRow(row)
            except Exception:
                pass
        # Refresh summary label if there is a label-like attr; keep safe/no hard dependency.
        try:
            for name in ("lbl_summary", "summary_label", "status_label", "lblStatus"):
                lab = getattr(self, name, None)
                if lab is not None and hasattr(lab, "setText"):
                    txt = lab.text()
                    if "ซ่อน" not in txt and deleted:
                        lab.setText(txt + f" | ซ่อนแล้ว {len(deleted)} รายการ")
                    break
        except Exception:
            pass

    def _soft_delete_selected(self):
        table = _find_table(self)
        if table is None:
            return
        rows = _selected_rows(table)
        if not rows:
            try:
                QMessageBox.information(self, "Queue", "กรุณาเลือกรายการ Queue ที่ต้องการเอาออกจากรายการ")
            except Exception:
                pass
            return
        try:
            reply = QMessageBox.question(
                self,
                "ยืนยันลบรายการ Queue",
                f"ต้องการเอารายการ Queue ที่เลือก {len(rows)} รายการออกจากหน้าจอหรือไม่?\n\n"
                "หมายเหตุ: ระบบจะซ่อนรายการนี้ถาวรจากหน้า Queue แต่จะไม่ลบไฟล์ PDF จริง และไม่ลบ OCR Cache",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        except Exception:
            pass
        deleted = _load_deleted(self)
        for row in rows:
            try:
                deleted.add(_row_key(table, row))
            except Exception:
                pass
        _save_deleted(self, deleted)
        _apply_filter(self)

    def _clear_hidden(self):
        deleted = _load_deleted(self)
        if not deleted:
            try:
                QMessageBox.information(self, "Queue", "ไม่มีรายการ Queue ที่ถูกซ่อนไว้")
            except Exception:
                pass
            return
        try:
            reply = QMessageBox.question(
                self,
                "คืนรายการ Queue ที่ซ่อนไว้",
                f"ต้องการคืนรายการ Queue ที่เคยซ่อนไว้ทั้งหมด {len(deleted)} รายการหรือไม่?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        except Exception:
            pass
        _save_deleted(self, set())
        # trigger any refresh-like method after clearing hidden list
        for nm in ("refresh", "reload", "load", "load_queue", "refresh_table", "populate_table", "load_status"):
            fn = getattr(self, nm, None)
            if callable(fn):
                try:
                    fn()
                    return
                except TypeError:
                    continue
                except Exception:
                    break
        _apply_filter(self)

    def _wire_buttons(self):
        # Queue table selection should select whole rows for easier deleting.
        table = _find_table(self)
        if table is not None:
            try:
                table.setSelectionBehavior(QAbstractItemView.SelectRows)
            except Exception:
                pass
        try:
            buttons = self.findChildren(QPushButton)
        except Exception:
            buttons = []
        delete_wired = False
        for btn in buttons:
            text = ""
            try:
                text = btn.text()
            except Exception:
                pass
            # Replace the existing delete-selected button behavior with persistent soft-delete.
            if "ลบรายการ Queue" in text or ("Queue" in text and "ลบ" in text):
                try:
                    btn.clicked.disconnect()
                except Exception:
                    pass
                try:
                    btn.setText("🗑 เอารายการ Queue ออกจากหน้าจอ")
                except Exception:
                    pass
                btn.clicked.connect(lambda _checked=False, self=self: _soft_delete_selected(self))
                delete_wired = True
            # After any refresh/clear button is clicked, re-apply hidden filter.
            if any(k in text for k in ("Refresh", "รีเฟรช", "ล้างสถานะ", "Clear")):
                try:
                    btn.clicked.connect(lambda _checked=False, self=self: QTimer.singleShot(80, lambda: _apply_filter(self)))
                    btn.clicked.connect(lambda _checked=False, self=self: QTimer.singleShot(250, lambda: _apply_filter(self)))
                except Exception:
                    pass
        # Add a small recovery button so accidental soft-delete can be reversed.
        if delete_wired and not getattr(self, "_queue_clear_hidden_btn_added", False):
            try:
                layout = self.layout()
                if layout is not None:
                    b = QPushButton("↩ คืนรายการ Queue ที่ซ่อนไว้")
                    b.clicked.connect(lambda _checked=False, self=self: _clear_hidden(self))
                    layout.addWidget(b)
                    self._queue_clear_hidden_btn_added = True
            except Exception:
                pass

    old_init = cls.__init__
    def new_init(self, *args, **kwargs):
        old_init(self, *args, **kwargs)
        try:
            _wire_buttons(self)
            QTimer.singleShot(0, lambda: _apply_filter(self))
        except Exception:
            pass
    cls.__init__ = new_init

    # Wrap common refresh/load methods so deleted rows never return after clearing status.
    for nm in ("refresh", "reload", "load", "load_queue", "refresh_table", "populate_table", "load_status"):
        fn = getattr(cls, nm, None)
        if callable(fn):
            def make_wrapper(name, orig):
                def wrapper(self, *args, **kwargs):
                    res = orig(self, *args, **kwargs)
                    try:
                        _wire_buttons(self)
                        _apply_filter(self)
                        QTimer.singleShot(80, lambda: _apply_filter(self))
                    except Exception:
                        pass
                    return res
                wrapper.__name__ = getattr(orig, "__name__", name)
                return wrapper
            setattr(cls, nm, make_wrapper(nm, fn))

    cls._queue_persistent_soft_delete_installed = True

_install_queue_persistent_soft_delete_patch()
# --- END_HOTFIX_QUEUE_PERSISTENT_SOFT_DELETE_V1 ---

