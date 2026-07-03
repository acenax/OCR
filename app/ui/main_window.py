"""Main application window hosting the three tabs."""
from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QTabWidget

from ..context import AppContext
from .customers_tab import CustomersTab
from .process_tab import ProcessTab
from .summary_tab import SummaryTab
from .settings_tab import SettingsTab


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

        self.tabs.addTab(self.customers_tab, "1) ลงทะเบียนลูกค้า")
        self.tabs.addTab(self.process_tab, "2) ประมวลผล PO")
        self.tabs.addTab(self.summary_tab, "3) สรุปรายเดือน")
        self.tabs.addTab(self.settings_tab, "4) ตั้งค่า")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)
        self.statusBar().showMessage("พร้อมใช้งาน")

    def _tab_changed(self, idx):
        if self.tabs.widget(idx) is self.summary_tab:
            self.summary_tab.refresh()

    def _settings_changed(self):
        self.process_tab.refresh_customers()
        self.summary_tab.refresh()
        self.customers_tab.refresh()

    def _customers_changed(self):
        # a customer was added or a mapping file imported -> refresh other tabs
        self.process_tab.refresh_customers()
        self.summary_tab.refresh()
