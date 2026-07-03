"""ตรวจความพร้อมของเครื่องก่อนรัน TMC AI OCR PROGRAM

รัน:  python check_env.py
จะบอกว่าอะไรพร้อม/ขาดอะไร
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

OK, BAD, WARN = "[ OK ]", "[FAIL]", "[WARN]"


def line(status, msg):
    print(f"{status} {msg}")


def main():
    print("=" * 60)
    print(" ตรวจความพร้อม TMC AI OCR PROGRAM")
    print("=" * 60)
    ok = True

    # 1) Python version
    v = sys.version_info
    if v >= (3, 10):
        line(OK, f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        ok = False
        line(BAD, f"Python {v.major}.{v.minor} (ต้องการ 3.10 ขึ้นไป)")

    # 2) Python packages
    pkgs = ["PySide6", "pandas", "openpyxl", "pdf2image",
            "pytesseract", "PIL", "cv2", "rapidfuzz", "numpy"]
    for p in pkgs:
        try:
            importlib.import_module(p)
            line(OK, f"ไลบรารี {p}")
        except Exception:
            ok = False
            line(BAD, f"ไลบรารี {p} ไม่พบ  ->  รัน install.bat")

    # 3) config-based detection of tesseract / poppler
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from app import config
    cfg = config.load_config()

    tpath = cfg["tesseract_path"]
    tfound = Path(tpath).exists() or shutil.which(tpath)
    if tfound:
        line(OK, f"Tesseract: {tpath}")
        try:
            exe = tpath if Path(tpath).exists() else "tesseract"
            langs = subprocess.run([exe, "--list-langs"], capture_output=True,
                                   text=True, timeout=15).stdout
            has_tha = "tha" in langs
            line(OK if has_tha else WARN,
                 "ภาษา tha (ไทย) " + ("ติดตั้งแล้ว" if has_tha
                                       else "ยังไม่มี — PO ภาษาไทยจะอ่านไม่ออก"))
        except Exception as e:
            line(WARN, f"เรียก tesseract --list-langs ไม่ได้: {e}")
    else:
        ok = False
        line(BAD, f"ไม่พบ Tesseract ({tpath}) -> ติดตั้ง หรือแก้ path ในแท็บตั้งค่า")

    ppath = cfg["poppler_path"]
    if ppath and Path(ppath).exists():
        line(OK, f"Poppler: {ppath}")
    elif shutil.which("pdftoppm"):
        line(OK, "Poppler: พบใน PATH")
    else:
        ok = False
        line(BAD, "ไม่พบ Poppler -> วางโฟลเดอร์ poppler ไว้ในโปรเจกต์ หรือแก้ path")

    # 4) data folders
    root = Path(cfg["root_folder"])
    line(OK if root.exists() else WARN, f"โฟลเดอร์หลัก: {root}")
    wh = Path(cfg["warehouse_file"])
    line(OK if wh.exists() else WARN,
         f"ไฟล์คลังสินค้า (stock_group_code): {wh}"
         + ("" if wh.exists() else "  <-- ไม่พบ แก้ path ในแท็บตั้งค่า"))

    print("=" * 60)
    print(" พร้อมใช้งาน! รัน run.bat ได้เลย" if ok
          else " ยังไม่พร้อม — แก้รายการ [FAIL] ด้านบนก่อน")
    print("=" * 60)


if __name__ == "__main__":
    main()
