"""Load shared warehouse data: the stock_group_code dropdown list and customer list."""
from __future__ import annotations

from pathlib import Path
import pandas as pd


def _pick_sheet(xl: pd.ExcelFile, wanted: str) -> str | None:
    for s in xl.sheet_names:
        if s.strip().lower() == wanted.lower():
            return s
    return None


def load_stock_group_codes(warehouse_file: str) -> list[str]:
    """Return the list of stock_group_code values for the dropdown.

    Source: the 'stock_group_code' sheet of คลังสินค้า.xlsx.
    """
    p = Path(warehouse_file)
    if not p.exists():
        return []
    xl = pd.ExcelFile(p)
    sheet = _pick_sheet(xl, "stock_group_code") or xl.sheet_names[0]
    df = xl.parse(sheet)
    if df.empty:
        return []
    col = df.columns[0]
    vals = (
        df[col].dropna().astype(str).map(str.strip).tolist()
    )
    # de-dup, keep order
    seen, out = set(), []
    for v in vals:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def load_customers(warehouse_file: str) -> list[tuple[str, str]]:
    """Return (code, name) customer rows from the 'ลูกค้า' sheet, if present."""
    p = Path(warehouse_file)
    if not p.exists():
        return []
    xl = pd.ExcelFile(p)
    sheet = _pick_sheet(xl, "ลูกค้า")
    if not sheet:
        return []
    df = xl.parse(sheet).dropna(how="all")
    if df.shape[1] >= 2:
        return [
            (str(a).strip(), str(b).strip())
            for a, b in zip(df.iloc[:, 0], df.iloc[:, 1])
            if str(a).strip()
        ]
    return [(str(a).strip(), "") for a in df.iloc[:, 0] if str(a).strip()]
