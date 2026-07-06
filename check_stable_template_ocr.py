from __future__ import annotations
import py_compile
from pathlib import Path

files = [
    Path("app/ocr_stable.py"),
    Path("app/ocr.py"),
]
ok = True
for f in files:
    try:
        py_compile.compile(str(f), doraise=True)
        print(f"{f}: OK")
    except Exception as e:
        ok = False
        print(f"{f}: FAILED -> {e}")
if ok:
    print("Stable template OCR patch syntax OK")
else:
    raise SystemExit(1)
