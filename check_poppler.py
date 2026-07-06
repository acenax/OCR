# -*- coding: utf-8 -*-
"""Quick Poppler checker for TMC_OCR."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys


def candidate_dirs() -> list[Path]:
    root = Path(__file__).resolve().parent
    cands = []
    for base in [root, root.parent, Path.cwd(), Path.cwd().parent]:
        cands.extend([
            base / "poppler" / "Library" / "bin",
            base / "poppler" / "bin",
            base / "poppler",
        ])
    cands.extend([
        Path(r"C:\poppler\Library\bin"),
        Path(r"C:\poppler\bin"),
        Path(r"C:\Program Files\poppler\Library\bin"),
        Path(r"C:\Program Files\poppler\bin"),
    ])
    out = []
    seen = set()
    for p in cands:
        s = str(p)
        if s not in seen:
            seen.add(s)
            out.append(p)
    return out


def valid_bin(p: Path) -> bool:
    return (p / "pdftoppm.exe").exists() or (p / "pdftocairo.exe").exists()


def main() -> int:
    which = shutil.which("pdftoppm") or shutil.which("pdftocairo")
    if which:
        print("พบ Poppler ใน PATH:", which)
        try:
            out = subprocess.run([which, "-v"], capture_output=True, text=True, timeout=10)
            print((out.stdout or out.stderr).strip().splitlines()[0])
        except Exception:
            pass
        return 0

    for p in candidate_dirs():
        if valid_bin(p):
            print("พบ Poppler:", p)
            print("ให้นำ path นี้ไปตั้งในแท็บ ตั้งค่า > Poppler (โฟลเดอร์ bin)")
            return 0

    print("ไม่พบ Poppler/pdftoppm.exe")
    print("แก้ได้ 2 วิธี:")
    print("1) นำโฟลเดอร์ poppler มาวางไว้ข้างโฟลเดอร์ TMC_OCR หรือใน TMC_OCR\\poppler")
    print("2) ตั้งค่า Poppler ไปที่โฟลเดอร์ bin ที่มี pdftoppm.exe เช่น C:\\poppler\\Library\\bin")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
