# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build spec for TMC AI OCR PROGRAM
# Bundles Tesseract-OCR (with Thai) and Poppler so the target PC needs neither.
import os

block_cipher = None

_tess = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR")

datas = [
    (r"..\poppler", "poppler"),          # PDF -> image
    (_tess, "Tesseract-OCR"),            # OCR engine + tessdata (eng, tha)
    ("templates", "templates"),          # learned customer profiles
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["rapidfuzz", "openpyxl"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6"],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TMC_OCR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TMC_OCR",
)
