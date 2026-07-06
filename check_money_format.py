# -*- coding: utf-8 -*-
from app.money_format import format_money, normalize_digits, is_money_header

print("=== Money Format Diagnostic ===")
print("26037.5 ->", format_money("26037.5"))
print("26037.50 ->", format_money("26037.50"))
print("1,822.63 ->", format_money("1,822.63"))
print("เลขไทย ๑๒๓๔๕.๖ ->", format_money("๑๒๓๔๕.๖"))
for h in ["ราคา", "ยอดเงิน OCR", "รวมราคาสินค้า", "VAT", "จำนวน", "รหัสสินค้า", "PO No."]:
    print(f"header {h!r}: money={is_money_header(h)}")
