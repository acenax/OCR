"""TMC AI OCR PROGRAM - entry point.

Run:  python main.py
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path


def _base_dir() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else Path(__file__).resolve().parent


def _trace(msg: str):
    """Append a startup trace line so we can see how far a packaged run gets."""
    try:
        with open(_base_dir() / "startup_trace.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}  {msg}\n")
    except Exception:
        pass


def main():
    _trace("=== start ===")
    from PySide6.QtWidgets import QApplication, QMessageBox
    _trace("PySide6 imported")
    app = QApplication(sys.argv)
    app.setApplicationName("TMC AI OCR PROGRAM")
    _trace("QApplication created")
    try:
        from app.context import AppContext
        from app.ui.main_window import MainWindow
        _trace("app modules imported")
        ctx = AppContext()
        _trace(f"AppContext ready (customers={ctx.customers()})")
        win = MainWindow(ctx)
        _trace("MainWindow built")
        win.showMaximized()
        _trace("window shown -> entering event loop")
        rc = app.exec()
        _trace(f"event loop exited rc={rc}")
        return rc
    except Exception:
        tb = traceback.format_exc()
        _trace("EXCEPTION:\n" + tb)
        try:
            (_base_dir() / "startup_error.log").write_text(tb, encoding="utf-8")
            QMessageBox.critical(None, "เริ่มโปรแกรมไม่สำเร็จ",
                                 "เกิดข้อผิดพลาด (ดู startup_error.log)\n\n" + tb[-1500:])
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
