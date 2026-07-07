
"""PaddleOCR local engine wrapper.

This module is intentionally small and defensive.  It supports PaddleOCR 2.7.x
(the recommended stable local engine for this project) and has a best-effort
fallback for PaddleOCR 3.x.  It returns text together with bounding boxes and
confidence, which is much more useful for table/PO extraction than Tesseract's
plain text output.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image


@dataclass(slots=True)
class OCRWord:
    text: str
    confidence: float
    box: list[list[float]]
    left: float
    top: float
    right: float
    bottom: float

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)


def _box_bounds(box: Any) -> tuple[float, float, float, float]:
    pts = box or []
    xs: list[float] = []
    ys: list[float] = []
    for p in pts:
        try:
            xs.append(float(p[0]))
            ys.append(float(p[1]))
        except Exception:
            continue
    if not xs or not ys:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def _normalise_v2_result(result: Any) -> list[OCRWord]:
    words: list[OCRWord] = []
    if result is None:
        return words

    # PaddleOCR 2.x for one image often returns: [ [box, (text, conf)], ... ]
    # Some versions wrap once more: [ [ [box, (text, conf)], ... ] ]
    lines: Iterable[Any]
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], list):
        first = result[0]
        if first and isinstance(first[0], (list, tuple)) and len(first[0]) >= 2 and isinstance(first[0][1], (list, tuple)):
            lines = first
        else:
            lines = result
    else:
        lines = result if isinstance(result, list) else []

    for line in lines:
        try:
            box = line[0]
            data = line[1]
            text = str(data[0]).strip()
            conf = float(data[1])
        except Exception:
            continue
        if not text:
            continue
        l, t, r, b = _box_bounds(box)
        words.append(OCRWord(text=text, confidence=conf, box=box, left=l, top=t, right=r, bottom=b))
    return words


def _normalise_v3_result(result: Any) -> list[OCRWord]:
    words: list[OCRWord] = []
    if not isinstance(result, list):
        return words
    for page in result:
        data: dict[str, Any] | None = None
        if hasattr(page, "json"):
            try:
                maybe = page.json
                data = maybe() if callable(maybe) else maybe
            except Exception:
                data = None
        if not isinstance(data, dict):
            try:
                data = dict(page)  # type: ignore[arg-type]
            except Exception:
                data = None
        if not isinstance(data, dict):
            continue
        rec_texts = data.get("rec_texts") or data.get("texts") or []
        rec_scores = data.get("rec_scores") or data.get("scores") or []
        rec_boxes = data.get("rec_polys") or data.get("dt_polys") or data.get("boxes") or []
        for text, conf, box in zip(rec_texts, rec_scores, rec_boxes):
            text = str(text).strip()
            if not text:
                continue
            l, t, r, b = _box_bounds(box)
            try:
                cf = float(conf)
            except Exception:
                cf = -1.0
            words.append(OCRWord(text=text, confidence=cf, box=box, left=l, top=t, right=r, bottom=b))
    return words


@lru_cache(maxsize=4)
def get_paddle_ocr(lang: str = "en") -> Any:
    from paddleocr import PaddleOCR

    lang = lang or os.environ.get("TMC_PADDLE_LANG", "en") or "en"

    # Stable PaddleOCR 2.7.x API.
    try:
        return PaddleOCR(lang=lang, use_angle_cls=True, use_gpu=False, show_log=False)
    except TypeError:
        pass

    # PaddleOCR 3.x API fallback.
    try:
        return PaddleOCR(lang=lang, use_textline_orientation=True, device="cpu")
    except TypeError:
        return PaddleOCR(lang=lang)


def is_paddle_available() -> bool:
    try:
        get_paddle_ocr("en")
        return True
    except Exception:
        return False


def extract_words(image: Image.Image, lang: str = "en", min_confidence: float = 0.20) -> list[OCRWord]:
    """Run PaddleOCR and return OCR words with coordinates.

    A temporary PNG path is used because it is the most compatible input format
    across PaddleOCR versions on Windows.
    """
    ocr = get_paddle_ocr(lang or "en")
    img = image.convert("RGB")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        img.save(tmp_path)
        if hasattr(ocr, "ocr"):
            result = ocr.ocr(str(tmp_path), cls=True)
            words = _normalise_v2_result(result)
        elif hasattr(ocr, "predict"):
            result = ocr.predict(str(tmp_path))
            words = _normalise_v3_result(result)
        else:
            words = []
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    return [w for w in words if w.confidence >= min_confidence or w.confidence < 0]


def words_to_text(words: list[OCRWord]) -> str:
    return " ".join(w.text for w in sorted(words, key=lambda x: (x.top, x.left))).strip()
