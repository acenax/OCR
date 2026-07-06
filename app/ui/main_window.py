from __future__ import annotations

# Phase 16: global money display format
try:
    from app.money_format import install_money_format_patch
    install_money_format_patch()
except Exception as _money_fmt_err:
    print("MONEY FORMAT PATCH disabled:", _money_fmt_err)

"""Main application window hosting the three tabs."""

from PySide6.QtWidgets import QMainWindow, QTabWidget

from ..context import AppContext
from .customers_tab import CustomersTab
from .process_tab import ProcessTab
from .summary_tab import SummaryTab
from .settings_tab import SettingsTab
from .tools_tab import ToolsTab


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("TMC AI OCR PROGRAM — อ่าน PO ลงระบบ")
        self.resize(1280, 780)

        self.tabs = QTabWidget()
        self.summary_tab = SummaryTab(ctx)
        self.process_tab = ProcessTab(ctx, on_saved=self.summary_tab.refresh)
        self.customers_tab = CustomersTab(ctx, on_changed=self._customers_changed)
        self.settings_tab = SettingsTab(ctx, on_changed=self._settings_changed)
        self.tools_tab = ToolsTab(ctx)

        self.tabs.addTab(self.customers_tab, "1) ลงทะเบียนลูกค้า")
        self.tabs.addTab(self.process_tab, "2) ประมวลผล PO")
        self.tabs.addTab(self.summary_tab, "3) สรุปรายเดือน")
        self.tabs.addTab(self.tools_tab, "4) เครื่องมือข้อมูล")
        self.tabs.addTab(self.settings_tab, "5) ตั้งค่า")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)
        self.statusBar().showMessage("พร้อมใช้งาน")

    def _tab_changed(self, idx):
        if self.tabs.widget(idx) is self.summary_tab:
            self.summary_tab.refresh()
        if hasattr(self, 'tools_tab'):
            self.tools_tab.refresh_customers()

    def _settings_changed(self):
        self.process_tab.refresh_customers()
        self.summary_tab.refresh()
        self.customers_tab.refresh()
        if hasattr(self, 'tools_tab'):
            self.tools_tab.refresh_customers()

    def _customers_changed(self):
        # a customer was added or a mapping file imported -> refresh other tabs
        self.process_tab.refresh_customers()
        self.summary_tab.refresh()

# === PHASE3 DASHBOARD TAB PATCH ===
try:
    from .dashboard_tab import DashboardTab as _Phase3DashboardTab

    _phase3_main_original_init = MainWindow.__init__

    def _phase3_main_init(self, ctx):
        _phase3_main_original_init(self, ctx)
        try:
            self.dashboard_tab = _Phase3DashboardTab(ctx)
            self.tabs.insertTab(0, self.dashboard_tab, "0) Dashboard")
            self.tabs.setCurrentIndex(0)
            self.statusBar().showMessage("พร้อมใช้งาน — ดูภาพรวมได้ที่ Dashboard")
        except Exception as exc:
            print("PHASE3 DASHBOARD disabled:", exc)

        try:
            self.tabs.currentChanged.connect(
                lambda idx: self.dashboard_tab.refresh()
                if hasattr(self, "dashboard_tab") and self.tabs.widget(idx) is self.dashboard_tab
                else None
            )
        except Exception:
            pass

    MainWindow.__init__ = _phase3_main_init
except Exception as _phase3_dashboard_error:
    print("PHASE3 DASHBOARD PATCH disabled:", _phase3_dashboard_error)
# === END PHASE3 DASHBOARD TAB PATCH ===

# === PHASE5 OCR QUEUE TAB PATCH ===
try:
    from .queue_tab import QueueTab as _Phase5QueueTab
    _phase5_main_original_init = MainWindow.__init__
    def _phase5_main_init(self, ctx):
        _phase5_main_original_init(self, ctx)
        try:
            self.queue_tab = _Phase5QueueTab(ctx)
            try:
                insert_at = 1 if self.tabs.count() > 0 and "Dashboard" in self.tabs.tabText(0) else min(3, self.tabs.count())
            except Exception:
                insert_at = min(3, self.tabs.count())
            self.tabs.insertTab(insert_at, self.queue_tab, "OCR Queue")
            self.tabs.currentChanged.connect(lambda idx: self.queue_tab.refresh() if hasattr(self, "queue_tab") and self.tabs.widget(idx) is self.queue_tab else None)
        except Exception as exc:
            print("PHASE5 QUEUE TAB disabled:", exc)
    MainWindow.__init__ = _phase5_main_init
except Exception as _phase5_main_error:
    print("PHASE5 OCR QUEUE TAB PATCH disabled:", _phase5_main_error)
# === END PHASE5 OCR QUEUE TAB PATCH ===

# === PHASE6 SMART IMPORT TAB PATCH ===
# This block is appended by apply_phase6_smart_import_detect_patch.py.
try:
    _phase6_old_main_window_init = MainWindow.__init__

    def _phase6_main_window_init(self, ctx):
        _phase6_old_main_window_init(self, ctx)
        try:
            def _phase6_imported_refresh():
                try:
                    if hasattr(self, "process_tab"):
                        self.process_tab.refresh_customers()
                except Exception:
                    pass
                try:
                    if hasattr(self, "dashboard_tab"):
                        self.dashboard_tab.refresh()
                except Exception:
                    pass
                try:
                    if hasattr(self, "queue_tab"):
                        self.queue_tab.refresh()
                except Exception:
                    pass
                try:
                    if hasattr(self, "summary_tab"):
                        self.summary_tab.refresh()
                except Exception:
                    pass
            insert_at = 1 if hasattr(self, "dashboard_tab") else 0
            self.tabs.insertTab(insert_at, self.smart_import_tab, "Smart Import")
        except Exception as exc:
            try:
                self.statusBar().showMessage(f"โหลด Smart Import ไม่สำเร็จ: {exc}")
            except Exception:
                print("โหลด Smart Import ไม่สำเร็จ:", exc)

    MainWindow.__init__ = _phase6_main_window_init
except Exception as _phase6_exc:
    print("PHASE6 SMART IMPORT TAB PATCH failed:", _phase6_exc)
# === END PHASE6 SMART IMPORT TAB PATCH ===
