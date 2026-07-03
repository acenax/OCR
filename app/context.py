"""Shared application state used by the UI."""
from __future__ import annotations

import pytesseract

from . import config, warehouse, pipeline
from .config import Config
from .matcher import ProductMatcher
from .storage import Store


class AppContext:
    def __init__(self):
        self.cfg: Config = config.load_config()
        pytesseract.pytesseract.tesseract_cmd = self.cfg["tesseract_path"]
        self.store = Store(str(config.DB_PATH))
        self.stock_group_codes: list[str] = []
        self.matcher: ProductMatcher | None = None
        self.current_customer: str = ""
        self.reload_warehouse()

    def reload_warehouse(self):
        self.stock_group_codes = warehouse.load_stock_group_codes(self.cfg["warehouse_file"])

    def customers(self) -> list[str]:
        return pipeline.customer_folders(self.cfg["root_folder"], self.cfg["po_subfolder"])

    def set_customer(self, customer: str):
        self.current_customer = customer
        pf = pipeline.product_file_for(
            self.cfg["root_folder"], customer, self.cfg["product_subfolder"])
        if pf:
            self.matcher = ProductMatcher.from_product_file(
                pf, self.cfg["fuzzy_threshold"], self.cfg["fuzzy_strong"])
        else:
            self.matcher = None

    def tmc_code_list(self) -> list[str]:
        if not self.matcher:
            return []
        seen, out = set(), []
        for tmc, _ in self.matcher._index.values():
            if tmc not in seen:
                seen.add(tmc)
                out.append(tmc)
        return sorted(out)

    def tmc_items(self) -> list[tuple[str, str]]:
        """(tmc_code, product_name) pairs for the searchable tmc picker."""
        if not self.matcher:
            return []
        seen, out = set(), []
        for tmc, name in self.matcher._index.values():
            if tmc not in seen:
                seen.add(tmc)
                out.append((tmc, name))
        return sorted(out)

    def tmc_to_name(self) -> dict[str, str]:
        """tmc_code -> canonical product name (first name seen)."""
        out: dict[str, str] = {}
        if not self.matcher:
            return out
        for tmc, name in self.matcher._index.values():
            out.setdefault(tmc, name)
        return out

    def apply_tesseract(self):
        pytesseract.pytesseract.tesseract_cmd = self.cfg["tesseract_path"]
