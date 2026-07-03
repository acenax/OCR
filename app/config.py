"""Application configuration, persisted to settings.json next to the app."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

# When packaged as a .exe (PyInstaller):
#   - IS_FROZEN True
#   - BUNDLE_DIR = read-only folder with bundled Tesseract/Poppler/templates
#   - APP_DIR    = folder next to the .exe = where we WRITE settings/db/templates
#   - PROJECT_ROOT (customer data) defaults next to the .exe (change in Settings)
IS_FROZEN = getattr(sys, "frozen", False)
if IS_FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
    PROJECT_ROOT = APP_DIR
else:
    APP_DIR = Path(__file__).resolve().parent.parent      # .../TMC_OCR
    BUNDLE_DIR = APP_DIR
    PROJECT_ROOT = APP_DIR.parent                         # .../TMC INVOICE INPUT

SETTINGS_PATH = APP_DIR / "settings.json"
DB_PATH = APP_DIR / "tmc_ocr.db"
TEMPLATE_DIR = APP_DIR / "templates"


def _autodetect_tesseract() -> str:
    candidates = [
        str(BUNDLE_DIR / "Tesseract-OCR" / "tesseract.exe"),   # bundled in the .exe
        str(APP_DIR / "Tesseract-OCR" / "tesseract.exe"),      # copied next to app
        str(PROJECT_ROOT / "Tesseract-OCR" / "tesseract.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return "tesseract"  # rely on PATH


def _autodetect_poppler() -> str:
    for p in (BUNDLE_DIR / "poppler" / "Library" / "bin",
              PROJECT_ROOT / "poppler" / "Library" / "bin"):
        if p.exists():
            return str(p)
    return ""  # rely on PATH


def _looks_like_data_root(folder: Path) -> bool:
    """A data root has a DATA\\ folder or a customer folder (…\\PO FROM CUSTOMER)."""
    try:
        if (folder / "DATA").exists():
            return True
        for child in folder.iterdir():
            if child.is_dir() and (child / "PO FROM CUSTOMER").exists():
                return True
    except Exception:
        pass
    return False


def _autodetect_root() -> str:
    """Where the customer folders + DATA live. In .exe mode, search the .exe folder
    and its parents so dropping the app inside the data folder just works."""
    if not IS_FROZEN:
        return str(PROJECT_ROOT)
    here = APP_DIR
    for cand in [here, *here.parents][:4]:
        if _looks_like_data_root(cand):
            return str(cand)
    return str(APP_DIR)


def _seed_templates():
    """Copy bundled customer profiles into the writable templates/ on first run."""
    src = BUNDLE_DIR / "templates"
    if not src.exists() or src == TEMPLATE_DIR:
        return
    TEMPLATE_DIR.mkdir(exist_ok=True)
    for f in src.glob("*.json"):
        dest = TEMPLATE_DIR / f.name
        if not dest.exists():
            try:
                shutil.copyfile(f, dest)
            except Exception:
                pass


_ROOT = _autodetect_root()

DEFAULTS = {
    "tesseract_path": _autodetect_tesseract(),
    "poppler_path": _autodetect_poppler(),
    "root_folder": _ROOT,
    "warehouse_file": str(Path(_ROOT) / "DATA" / "คลังสินค้า.xlsx"),
    "dpi": 300,
    "ocr_lang": "tha+eng",          # Thai + English (PO descriptions can be either)
    "fuzzy_threshold": 72,          # >= this score => auto fuzzy match
    "fuzzy_strong": 90,             # >= this => treated as confident match
    # sub-folder names inside each customer folder
    "po_subfolder": "PO FROM CUSTOMER",
    "product_subfolder": "PRODUCT DETAIL",
    "invoice_subfolder": "INVOICE FILE",
}


class Config:
    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data.get(key, DEFAULTS.get(key))

    def get(self, key, default=None):
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key, value):
        self._data[key] = value

    @property
    def data(self) -> dict:
        return self._data

    def save(self):
        SETTINGS_PATH.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def load_config() -> Config:
    data = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        try:
            data.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    TEMPLATE_DIR.mkdir(exist_ok=True)
    _seed_templates()
    return Config(data)
