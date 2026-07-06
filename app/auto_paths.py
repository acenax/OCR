"""Auto-discovery helpers for Tesseract and Poppler.

Goal: make the app portable across Windows machines without requiring the user
manually type paths in Settings. The scan is intentionally limited to likely
folders so startup stays fast.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable

IS_FROZEN = getattr(sys, "frozen", False)
if IS_FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    BUNDLE_DIR = APP_DIR

TESSERACT_NAMES = {"tesseract.exe", "tesseract"}
PDFINFO_NAMES = {"pdfinfo.exe", "pdfinfo"}
RENDERER_NAMES = {"pdftoppm.exe", "pdftoppm", "pdftocairo.exe", "pdftocairo"}


def _clean(value) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _existing_path(value) -> Path | None:
    s = _clean(value)
    if not s:
        return None
    try:
        p = Path(os.path.expandvars(s)).expanduser()
        if p.exists():
            return p.resolve()
    except Exception:
        return None
    return None


def _unique(values: Iterable) -> list:
    out = []
    seen = set()
    for v in values:
        if v is None:
            continue
        s = _clean(v)
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(v)
    return out


def valid_tesseract(path) -> str:
    """Return valid tesseract executable path, 'tesseract' for PATH, or ''."""
    s = _clean(path)
    if not s:
        return ""
    if s.lower() == "tesseract" and shutil.which("tesseract"):
        return "tesseract"
    p = _existing_path(s)
    if not p:
        return ""
    if p.is_file() and p.name.lower() in TESSERACT_NAMES:
        return str(p)
    if p.is_dir():
        for name in ("tesseract.exe", "tesseract"):
            cand = p / name
            if cand.exists():
                return str(cand.resolve())
        cand = p / "Tesseract-OCR" / "tesseract.exe"
        if cand.exists():
            return str(cand.resolve())
    return ""


def _depth_limited_walk(root: Path, max_depth: int = 5, max_dirs: int = 2500):
    root = Path(root)
    if not root.exists() or not root.is_dir():
        return
    root_depth = len(root.parts)
    count = 0
    skip_names = {
        ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
        "Windows", "System32", "$Recycle.Bin", "Temp",
    }
    stack = [root]
    while stack:
        cur = stack.pop()
        count += 1
        if count > max_dirs:
            return
        try:
            yield cur
            if len(cur.parts) - root_depth >= max_depth:
                continue
            for child in cur.iterdir():
                if child.is_dir() and child.name not in skip_names:
                    stack.append(child)
        except Exception:
            continue


def _scan_for_file(roots: Iterable, names: set[str], max_depth: int = 5) -> str:
    for root in _unique(roots):
        p = _existing_path(root)
        if not p or not p.is_dir():
            continue
        for folder in _depth_limited_walk(p, max_depth=max_depth):
            for name in names:
                cand = folder / name
                if cand.exists():
                    return str(cand.resolve())
    return ""


def find_tesseract(configured: str = "", start_dirs: Iterable | None = None) -> str:
    """Find tesseract.exe from configured path, bundle, common install paths, or PATH."""
    current = valid_tesseract(configured)
    if current:
        return current

    env_candidates = [
        os.environ.get("TESSERACT_CMD"),
        os.environ.get("TESSERACT_PATH"),
    ]

    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")

    candidates = env_candidates + [
        shutil.which("tesseract"),
        BUNDLE_DIR / "Tesseract-OCR" / "tesseract.exe",
        BUNDLE_DIR / "_internal" / "Tesseract-OCR" / "tesseract.exe",
        APP_DIR / "Tesseract-OCR" / "tesseract.exe",
        APP_DIR / "_internal" / "Tesseract-OCR" / "tesseract.exe",
        APP_DIR / "dist" / "TMC_OCR" / "_internal" / "Tesseract-OCR" / "tesseract.exe",
        APP_DIR.parent / "Tesseract-OCR" / "tesseract.exe",
        Path(local) / "Programs" / "Tesseract-OCR" / "tesseract.exe" if local else None,
        Path(pf) / "Tesseract-OCR" / "tesseract.exe",
        Path(pf86) / "Tesseract-OCR" / "tesseract.exe",
    ]
    for c in _unique(candidates):
        found = valid_tesseract(c)
        if found:
            return found

    scan_roots = list(start_dirs or []) + [
        APP_DIR,
        APP_DIR / "dist",
        APP_DIR.parent,
        Path.home() / "Desktop",
        Path(local) / "Programs" if local else None,
    ]
    found = _scan_for_file(scan_roots, {"tesseract.exe", "tesseract"}, max_depth=5)
    return found or ""


def valid_poppler_bin(path) -> str:
    """Return a Poppler bin folder only if pdfinfo + renderer exist."""
    s = _clean(path)
    if not s:
        return ""
    p = _existing_path(s)
    if not p:
        return ""
    if p.is_file() and p.name.lower() in (PDFINFO_NAMES | RENDERER_NAMES):
        p = p.parent

    candidates = [
        p,
        p / "bin",
        p / "Library" / "bin",
        p / "poppler" / "bin",
        p / "poppler" / "Library" / "bin",
        p / "_internal" / "poppler" / "Library" / "bin",
    ]
    for c in candidates:
        try:
            has_pdfinfo = any((c / n).exists() for n in PDFINFO_NAMES)
            has_renderer = any((c / n).exists() for n in RENDERER_NAMES)
            if has_pdfinfo and has_renderer:
                return str(c.resolve())
        except Exception:
            continue
    return ""


def _scan_for_poppler_bin(roots: Iterable, max_depth: int = 6) -> str:
    for root in _unique(roots):
        p = _existing_path(root)
        if not p or not p.is_dir():
            continue
        direct = valid_poppler_bin(p)
        if direct:
            return direct
        for folder in _depth_limited_walk(p, max_depth=max_depth, max_dirs=3500):
            direct = valid_poppler_bin(folder)
            if direct:
                return direct
    return ""


def find_poppler(configured: str = "", start_dirs: Iterable | None = None) -> str:
    """Find Poppler bin folder. Return '' when Poppler is already available in PATH."""
    current = valid_poppler_bin(configured)
    if current:
        return current

    env_candidates = [
        os.environ.get("POPPLER_PATH"),
        os.environ.get("POPPLER_HOME"),
    ]
    candidates = env_candidates + [
        BUNDLE_DIR / "poppler" / "Library" / "bin",
        BUNDLE_DIR / "poppler" / "bin",
        BUNDLE_DIR / "_internal" / "poppler" / "Library" / "bin",
        APP_DIR / "poppler" / "Library" / "bin",
        APP_DIR / "poppler" / "bin",
        APP_DIR / "_internal" / "poppler" / "Library" / "bin",
        APP_DIR / "dist" / "TMC_OCR" / "_internal" / "poppler" / "Library" / "bin",
        APP_DIR.parent / "poppler" / "Library" / "bin",
        APP_DIR.parent / "poppler" / "bin",
        r"C:\poppler\Library\bin",
        r"C:\poppler\bin",
        r"C:\Program Files\poppler\Library\bin",
        r"C:\Program Files\poppler\bin",
        r"C:\Program Files (x86)\poppler\Library\bin",
        r"C:\Program Files (x86)\poppler\bin",
    ]
    for c in _unique(candidates):
        found = valid_poppler_bin(c)
        if found:
            return found

    has_pdfinfo = shutil.which("pdfinfo")
    has_renderer = shutil.which("pdftoppm") or shutil.which("pdftocairo")
    if has_pdfinfo and has_renderer:
        return ""

    scan_roots = list(start_dirs or []) + [
        APP_DIR,
        APP_DIR / "dist",
        APP_DIR.parent,
        Path.home() / "Desktop",
    ]
    return _scan_for_poppler_bin(scan_roots, max_depth=6)


def apply_auto_paths(cfg, save: bool = True, start_dirs: Iterable | None = None) -> dict:
    """Update cfg with working Tesseract/Poppler paths when current paths are invalid."""
    starts = list(start_dirs or [])
    try:
        rf = cfg.get("root_folder", "")
        if rf:
            starts.extend([rf, Path(rf).parent])
    except Exception:
        pass
    starts.extend([APP_DIR, APP_DIR.parent])

    old_tess = cfg.get("tesseract_path", "") if hasattr(cfg, "get") else ""
    old_popp = cfg.get("poppler_path", "") if hasattr(cfg, "get") else ""

    tess = find_tesseract(old_tess, starts)
    popp = find_poppler(old_popp, starts)

    changed = False
    if tess and tess != old_tess:
        cfg.set("tesseract_path", tess)
        changed = True
    if popp != old_popp and (popp or not valid_poppler_bin(old_popp)):
        cfg.set("poppler_path", popp)
        changed = True

    if changed and save:
        try:
            cfg.save()
        except Exception:
            pass
    return {
        "changed": changed,
        "tesseract_path": tess or old_tess,
        "poppler_path": popp,
        "tesseract_valid": bool(tess),
        "poppler_valid": bool(popp) or bool(shutil.which("pdfinfo") and (shutil.which("pdftoppm") or shutil.which("pdftocairo"))),
    }


def diagnostic(configured_tesseract: str = "", configured_poppler: str = "", start_dirs: Iterable | None = None) -> str:
    tess = find_tesseract(configured_tesseract, start_dirs)
    popp = find_poppler(configured_poppler, start_dirs)
    lines = ["=== Auto Path Diagnostic ==="]
    lines.append(f"Tesseract: {tess or 'NOT FOUND'}")
    if popp:
        lines.append(f"Poppler:   {popp}")
    elif shutil.which("pdfinfo") and (shutil.which("pdftoppm") or shutil.which("pdftocairo")):
        lines.append("Poppler:   FOUND IN PATH")
    else:
        lines.append("Poppler:   NOT FOUND")
    return "\n".join(lines)
