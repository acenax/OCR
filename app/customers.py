"""Customer registration: create the folder structure and attach a customer's
own product-name -> tmc_code mapping file (Product Details.xlsx).

Each customer has a DIFFERENT product-naming scheme, so every customer keeps its
own mapping file under <customer>/<product_subfolder>/Product Details.xlsx.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pandas as pd

from .config import Config
from . import template


class RegisterError(Exception):
    pass


def _safe_name(name: str) -> str:
    name = name.strip()
    if not name or re.search(r'[\\/:*?"<>|]', name):
        raise RegisterError("ชื่อลูกค้าไม่ถูกต้อง (ห้ามมีอักขระ \\ / : * ? \" < > |)")
    return name


def create_customer(cfg: Config, name: str) -> str:
    """Create <root>/<name>/{PO, PRODUCT DETAIL, INVOICE FILE}. Returns the path."""
    name = _safe_name(name)
    base = Path(cfg["root_folder"]) / name
    if base.exists():
        raise RegisterError(f"มีลูกค้าชื่อ '{name}' อยู่แล้ว")
    for sub in (cfg["po_subfolder"], cfg["product_subfolder"], cfg["invoice_subfolder"]):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return str(base)


def validate_product_file(path: str) -> tuple[bool, str, int]:
    """Check the mapping file has a tmc_code column + at least one name column.

    Returns (ok, message, n_rows_with_tmc).
    """
    p = Path(path)
    if not p.exists():
        return False, "ไม่พบไฟล์", 0
    try:
        df = pd.read_excel(p)
    except Exception as e:
        return False, f"เปิดไฟล์ไม่ได้: {e}", 0
    tmc_col = next((c for c in df.columns if "tmc" in str(c).lower()), None)
    if tmc_col is None:
        return False, "ไม่พบคอลัมน์ 'tmc_code' ในไฟล์", 0
    if list(df.columns).index(tmc_col) == 0:
        return False, "ต้องมีคอลัมน์ 'ชื่อสินค้าลูกค้า' อยู่ก่อนคอลัมน์ tmc_code", 0
    n = int(df[tmc_col].notna().sum())
    if n == 0:
        return False, "คอลัมน์ tmc_code ว่างทั้งหมด", 0
    return True, f"พบสินค้าที่มี tmc_code จำนวน {n} รายการ", n


def import_product_file(cfg: Config, customer: str, src_path: str) -> str:
    """Copy the customer's mapping file into <customer>/<product_subfolder>/Product Details.xlsx."""
    ok, msg, _ = validate_product_file(src_path)
    if not ok:
        raise RegisterError(msg)
    dest_dir = Path(cfg["root_folder"]) / customer / cfg["product_subfolder"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "Product Details.xlsx"
    shutil.copyfile(src_path, dest)
    return str(dest)


def import_po_files(cfg: Config, customer: str, src_paths: list[str]) -> tuple[list[str], list[str]]:
    """Copy PO PDFs into <customer>/<po_subfolder>/ (folder auto-created).

    Returns (copied_paths, skipped_names). Existing files with the same name are skipped.
    """
    if not customer:
        raise RegisterError("ยังไม่ได้เลือกลูกค้า")
    dest_dir = Path(cfg["root_folder"]) / customer / cfg["po_subfolder"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied, skipped = [], []
    for s in src_paths:
        src = Path(s)
        dest = dest_dir / src.name
        if dest.exists():
            skipped.append(src.name)
            continue
        shutil.copy2(src, dest)
        copied.append(str(dest))
    return copied, skipped


def customer_status(cfg: Config, customer: str) -> dict:
    """Summary shown in the registration tab."""
    from .pipeline import product_file_for
    pf = product_file_for(cfg["root_folder"], customer, cfg["product_subfolder"])
    n_products = 0
    if pf:
        try:
            df = pd.read_excel(pf)
            tmc_col = next((c for c in df.columns if "tmc" in str(c).lower()), None)
            n_products = int(df[tmc_col].notna().sum()) if tmc_col else 0
        except Exception:
            n_products = 0
    po_dir = Path(cfg["root_folder"]) / customer / cfg["po_subfolder"]
    n_po = len(list(po_dir.glob("*.pdf"))) if po_dir.exists() else 0
    return {
        "customer": customer,
        "has_product_file": bool(pf),
        "product_file": pf,
        "n_products": n_products,
        "n_po": n_po,
        "has_template": template.exists(customer),
    }
