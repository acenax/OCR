"""Data models for the OCR pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class POLine:
    """One product line read from a PO."""
    item_no: str = ""              # ITEM / ลำดับ
    product_code_raw: str = ""     # PRODUCT CODE / รหัสสินค้า (customer internal code, OCR)
    description_raw: str = ""      # DESCRIPTION / รายการ (real part number, OCR)
    qty: float = 0.0               # Q'TY / จำนวน
    price: float = 0.0             # UNIT PRICE / หน่วยละ
    amount: float = 0.0            # AMOUNT / จำนวนเงิน

    # Filled by the matcher / user:
    tmc_code: str = ""             # looked up from the customer's Product Details.xlsx
    matched_name: str = ""         # canonical product name of the matched tmc_code
    stock_group_code: str = ""     # chosen from warehouse dropdown
    match_score: float = 0.0       # 0..100 fuzzy-match confidence
    match_status: str = "no_match"  # matched | fuzzy | no_match | manual

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PODocument:
    """Everything read from a single PO PDF."""
    source_pdf: str = ""
    customer: str = ""             # customer folder name
    po_no: str = ""                # เลขที่ PO
    po_date: str = ""              # วันที่ออกเอกสาร (ISO yyyy-mm-dd if parsed)
    po_date_raw: str = ""          # date exactly as printed
    total: float = 0.0             # รวมราคาสินค้า
    vat: float = 0.0               # ภาษีมูลค่าเพิ่ม
    grand_total: float = 0.0       # รวมเงินทั้งสิ้น
    lines: list[POLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def month(self) -> str:
        """yyyy-MM bucket used for the monthly summary, from the PO date."""
        if self.po_date and len(self.po_date) >= 7:
            return self.po_date[:7]
        return ""

    @property
    def item_count(self) -> int:
        return len(self.lines)
