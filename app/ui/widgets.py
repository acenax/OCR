"""Small reusable widgets."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox
from PySide6.QtCore import Qt


class SearchCombo(QComboBox):
    """Editable combo with type-to-search completion."""
    def __init__(self, items: list[str], current: str = "", parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.addItem("")
        self.addItems(items)
        self.setCurrentText(current)
        self.completer().setCompletionMode(self.completer().CompletionMode.PopupCompletion)
        self.completer().setFilterMode(Qt.MatchContains)
        self.setMaxVisibleItems(20)

    def value(self) -> str:
        return self.currentText().strip()


class TmcCombo(QComboBox):
    """tmc_code picker that is searchable by product NAME as well as code.

    Each item shows 'tmc_code  |  product name' but value() returns only the code,
    so the user can type part of a Thai/English product name to find the right code.
    """
    def __init__(self, items: list[tuple[str, str]], current_code: str = "", parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.addItem("", "")
        for code, name in items:
            self.addItem(f"{code}  |  {name}", code)
        self.setMaxVisibleItems(20)
        comp = self.completer()
        comp.setCompletionMode(comp.CompletionMode.PopupCompletion)
        comp.setFilterMode(Qt.MatchContains)
        self.set_code(current_code)

    def set_code(self, code: str):
        if not code:
            self.setCurrentIndex(0)
            return
        i = self.findData(code)
        if i >= 0:
            self.setCurrentIndex(i)
        else:
            self.setEditText(code)

    def value(self) -> str:
        i = self.currentIndex()
        if i > 0 and self.itemData(i):
            return self.itemData(i)
        t = self.currentText().strip()
        return t.split("|")[0].strip() if "|" in t else t
