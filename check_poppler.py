from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def has_required(bin_dir: Path):
    p = Path(str(bin_dir).strip().strip('"'))
    if p.is_file():
        p = p.parent
    pdfinfo = (p / "pdfinfo.exe").exists() or (p / "pdfinfo").exists()
    renderer = (p / "pdftoppm.exe").exists() or (p / "pdftocairo.exe").exists() or (p / "pdftoppm").exists() or (p / "pdftocairo").exists()
    return pdfinfo, renderer


def variants(x):
    if not x:
        return []
    p = Path(str(x).strip().strip('"'))
    out = [p]
    if p.is_file():
        out = [p.parent]
    else:
        out += [p / "Library" / "bin", p / "bin", p / "poppler" / "Library" / "bin", p / "poppler" / "bin"]
    return out


raw = []
settings = ROOT / "settings.json"
if settings.exists():
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
        raw.append(data.get("poppler_path"))
    except Exception as e:
        print("อ่าน settings.json ไม่ได้:", e)

for k in ("POPPLER_PATH", "POPPLER_HOME"):
    if os.environ.get(k):
        raw.append(os.environ.get(k))

raw += [
    ROOT / "poppler" / "Library" / "bin",
    ROOT / "poppler" / "bin",
    ROOT / "poppler",
    ROOT.parent / "poppler" / "Library" / "bin",
    ROOT.parent / "poppler" / "bin",
    ROOT.parent / "poppler",
    Path(r"D:\PROJECT\TMC INVOICE INPUT\poppler\Library\bin"),
    Path(r"D:\PROJECT\TMC INVOICE INPUT\poppler\bin"),
    Path(r"D:\PROJECT\TMC INVOICE INPUT\poppler"),
    Path(r"C:\poppler\Library\bin"),
    Path(r"C:\poppler\bin"),
    Path(r"C:\poppler"),
]

for exe in ("pdfinfo", "pdfinfo.exe", "pdftoppm", "pdftoppm.exe", "pdftocairo", "pdftocairo.exe"):
    found = shutil.which(exe)
    if found:
        raw.append(Path(found).parent)

seen = set()
print("=== Poppler Diagnostic ===")
valid = []
checked = 0
for r in raw:
    for c in variants(r):
        key = str(c).lower()
        if key in seen:
            continue
        seen.add(key)
        if not c.exists():
            continue
        checked += 1
        pdfinfo, renderer = has_required(c)
        status = "OK" if pdfinfo and renderer else "MISSING"
        print(f"[{status}] {c}")
        print(f"  pdfinfo: {'OK' if pdfinfo else 'NO'}")
        print(f"  renderer(pdftoppm/pdftocairo): {'OK' if renderer else 'NO'}")
        if pdfinfo and renderer:
            valid.append(c)

print()
if valid:
    print("พบ Poppler ที่ใช้งานได้:")
    print(valid[0])
    print("\nให้นำ path นี้ไปใส่ในแท็บ ตั้งค่า > Poppler bin หรือปล่อยให้ hotfix ตั้งค่าให้")
else:
    print("ยังไม่พบ Poppler ที่ครบ")
    print("ต้องมีทั้ง pdfinfo.exe และ pdftoppm.exe หรือ pdftocairo.exe ในโฟลเดอร์ bin เดียวกัน")
    print("ตัวอย่าง path ที่ถูกต้อง: C:\\poppler\\Library\\bin")

print(f"\nตรวจโฟลเดอร์ที่มีอยู่ทั้งหมด: {checked} รายการ")
