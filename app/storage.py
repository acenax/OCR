"""SQLite storage for the monthly summary and saved invoice line items."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .models import PODocument


SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer TEXT NOT NULL,
    po_no TEXT NOT NULL,
    po_date TEXT,           -- ISO yyyy-mm-dd
    month TEXT,             -- yyyy-MM
    item_count INTEGER,
    total REAL,
    vat REAL,
    grand_total REAL,
    excel_path TEXT,
    source_pdf TEXT,
    created_at TEXT,
    UNIQUE(customer, po_no)
);
CREATE TABLE IF NOT EXISTS invoice_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    tmc_code TEXT,
    stock_group_code TEXT,
    qty REAL,
    price REAL,
    description_raw TEXT,
    product_code_raw TEXT,
    match_score REAL,
    FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
);
"""


class Store:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        con = self._con()
        con.executescript(SCHEMA)
        con.commit()
        con.close()

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.execute("PRAGMA foreign_keys = ON")
        con.row_factory = sqlite3.Row
        return con

    def save_invoice(self, doc: PODocument, excel_path: str) -> int:
        con = self._con()
        cur = con.cursor()
        # upsert: replace any prior record for this customer+PO
        cur.execute("SELECT id FROM invoices WHERE customer=? AND po_no=?",
                    (doc.customer, doc.po_no))
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM invoices WHERE id=?", (row["id"],))
        cur.execute(
            """INSERT INTO invoices
               (customer, po_no, po_date, month, item_count, total, vat,
                grand_total, excel_path, source_pdf, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (doc.customer, doc.po_no, doc.po_date, doc.month, doc.item_count,
             doc.total, doc.vat, doc.grand_total, excel_path, doc.source_pdf,
             datetime.now().isoformat(timespec="seconds")),
        )
        inv_id = cur.lastrowid
        for l in doc.lines:
            cur.execute(
                """INSERT INTO invoice_items
                   (invoice_id, tmc_code, stock_group_code, qty, price,
                    description_raw, product_code_raw, match_score)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (inv_id, l.tmc_code, l.stock_group_code, l.qty, l.price,
                 l.description_raw, l.product_code_raw, l.match_score),
            )
        con.commit()
        con.close()
        return inv_id

    def list_invoices(self, date_from: str = "", date_to: str = "",
                      customer: str = "") -> list[dict]:
        q = "SELECT * FROM invoices WHERE 1=1"
        args: list = []
        if date_from:
            q += " AND po_date >= ?"
            args.append(date_from)
        if date_to:
            q += " AND po_date <= ?"
            args.append(date_to)
        if customer:
            q += " AND customer = ?"
            args.append(customer)
        q += " ORDER BY po_date DESC, id DESC"
        con = self._con()
        rows = [dict(r) for r in con.execute(q, args).fetchall()]
        con.close()
        return rows

    def items_for(self, invoice_id: int) -> list[dict]:
        con = self._con()
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM invoice_items WHERE invoice_id=?", (invoice_id,)).fetchall()]
        con.close()
        return rows

    def delete_invoice(self, invoice_id: int):
        con = self._con()
        con.execute("DELETE FROM invoices WHERE id=?", (invoice_id,))
        con.commit()
        con.close()

    def delete_old(self, keep_months: int = 1):
        """Spec: keep ~1 month. Optional housekeeping helper (not auto-run)."""
        con = self._con()
        con.execute(
            "DELETE FROM invoices WHERE month < strftime('%Y-%m','now','-{} month')".format(keep_months))
        con.commit()
        con.close()

# === PHASE12 CLEANUP DELETE ARABIC PATCH ===
try:
    from .arabic_digits import normalize_obj_digits as _phase12_norm_doc, to_arabic_digits as _phase12_digits
    _phase12_old_save_invoice = Store.save_invoice
    _phase12_old_list_invoices = Store.list_invoices
    def _phase12_save_invoice(self, doc, excel_path):
        _phase12_norm_doc(doc)
        excel_path = _phase12_digits(excel_path)
        return _phase12_old_save_invoice(self, doc, excel_path)
    def _phase12_list_invoices(self, *args, **kwargs):
        rows = _phase12_old_list_invoices(self, *args, **kwargs)
        for row in rows:
            for k, v in list(row.items()):
                if isinstance(v, str):
                    row[k] = _phase12_digits(v)
        return rows
    Store.save_invoice = _phase12_save_invoice
    Store.list_invoices = _phase12_list_invoices
except Exception:
    pass
