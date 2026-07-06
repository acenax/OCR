# -*- coding: utf-8 -*-
from pathlib import Path
import py_compile

files = [Path('app/ocr_tuning.py'), Path('app/ocr.py')]
print('=== OCR tuning check ===')
for f in files:
    try:
        py_compile.compile(str(f), doraise=True)
        print(f'[OK] {f}')
    except Exception as e:
        print(f'[ERROR] {f}: {e}')
print('หมายเหตุ: หลัง patch ให้กดล้าง OCR Cache ทั้งหมด แล้ว OCR ใหม่')
