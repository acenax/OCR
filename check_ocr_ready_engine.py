from __future__ import annotations

import ast
from pathlib import Path

FILES = [
    "app/ocr.py",
    "app/ocr_template_v2.py",
    "app/ocr_numbers.py",
    "app/ocr_text.py",
    "app/ocr_image_filters.py",
    "app/ocr_debug.py",
    "app/ui/layout_teacher.py",
]

ok = True
for name in FILES:
    path = Path(name)
    try:
        ast.parse(path.read_text(encoding="utf-8"))
        print(f"{name}: OK")
    except Exception as exc:
        ok = False
        print(f"{name}: FAILED - {exc}")
if not ok:
    raise SystemExit(1)
print("OCR Ready Engine check passed.")
