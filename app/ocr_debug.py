"""Optional OCR debug-crop writer.

Set environment variable TMC_OCR_DEBUG=1 to save crops and JSON results.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from PIL import Image


def enabled() -> bool:
    return os.environ.get("TMC_OCR_DEBUG", "").strip() in {"1", "true", "TRUE", "yes", "YES"}


def save_crop(root: str | Path, customer: str, po_name: str, name: str, image: Image.Image, data: dict[str, Any] | None = None) -> None:
    if not enabled():
        return
    try:
        out_dir = Path(root) / "debug_ocr" / (customer or "unknown") / (po_name or "document")
        out_dir.mkdir(parents=True, exist_ok=True)
        image.save(out_dir / f"{name}.png")
        if data is not None:
            (out_dir / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
