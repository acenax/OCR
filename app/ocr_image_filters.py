from __future__ import annotations

"""Image preprocessing profiles for OCR.

The goal is to make faint scans, colour-shifted scans and documents with
red/green/blue scanner lines easier for Tesseract to read.  Keep this module
small and dependency-light: OpenCV/Pillow are already used by the OCR pipeline.
"""

from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

_DIGIT_TRANS = str.maketrans(
    "๐๑๒๓๔๕๖๗๘๙٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹０１２３４５６７８９",
    "0123456789012345678901234567890123456789",
)

PROFILE_LABELS = {
    "auto": "อัตโนมัติ (แนะนำ)",
    "faded": "เอกสารจาง / สีซีด",
    "strong": "เข้มมาก / ตัวหนังสือจางมาก",
    "line_clean": "ลบเส้นสีรบกวน",
    "numeric": "เน้นตัวเลข ราคา จำนวนเงิน",
    "raw": "ไม่ปรับภาพ",
}


def normalize_digits(text: Any) -> str:
    return str(text or "").translate(_DIGIT_TRANS)


def _pil_to_rgb_array(pil_img: Image.Image) -> np.ndarray:
    return np.array(pil_img.convert("RGB"))


def remove_colored_artifacts(rgb: np.ndarray, aggressive: bool = True) -> np.ndarray:
    """Remove highly saturated coloured scanner artifacts.

    Many CMT scans have red/green/blue horizontal lines crossing the table.
    The mask removes saturated coloured pixels but keeps dark text/table lines.
    """
    out = rgb.copy()
    hsv = cv2.cvtColor(out, cv2.COLOR_RGB2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    gray = cv2.cvtColor(out, cv2.COLOR_RGB2GRAY)

    sat_threshold = 45 if aggressive else 70
    mask = (sat > sat_threshold) & (val > 60) & (gray > 70)
    out[mask] = [255, 255, 255]

    # Extra pass: remove long coloured horizontal strokes.  This is deliberately
    # narrow so it does not wipe normal black table borders.
    mask_u8 = mask.astype("uint8") * 255
    h, w = mask_u8.shape
    if w > 50:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, w // 8), 1))
        horiz = cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, kernel)
        out[horiz > 0] = [255, 255, 255]
    return out


def apply_ocr_filter(
    pil_img: Image.Image,
    profile: str = "auto",
    *,
    numeric: bool = False,
    remove_lines: bool = True,
    threshold: bool = False,
) -> Image.Image:
    """Return an image prepared for OCR/preview.

    profile:
      auto       conservative default
      faded      stronger contrast for light scans
      strong     high contrast + sharper threshold
      line_clean focus on coloured line removal
      numeric    optimized for price/qty/amount crops
      raw        keep as close to original as possible
    """
    profile = str(profile or "auto").strip().lower()
    if profile not in PROFILE_LABELS:
        profile = "auto"

    if profile == "raw":
        return pil_img.convert("RGB")

    rgb = _pil_to_rgb_array(pil_img)
    if remove_lines or profile in {"auto", "line_clean", "strong", "numeric"}:
        rgb = remove_colored_artifacts(rgb, aggressive=profile in {"line_clean", "strong", "numeric"})

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    # Background normalization.  Helps with blue/green paper casts without
    # making black text too thick.
    if profile in {"auto", "faded", "line_clean", "numeric"}:
        gray = cv2.fastNlMeansDenoising(gray, None, 8, 7, 21)
        clip = 2.0 if profile == "auto" else 2.8
        gray = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8)).apply(gray)

    if profile == "strong":
        gray = cv2.createCLAHE(clipLimit=3.5, tileGridSize=(6, 6)).apply(gray)

    if profile == "faded":
        pil = Image.fromarray(gray)
        pil = ImageOps.autocontrast(pil, cutoff=1)
        pil = ImageEnhance.Contrast(pil).enhance(1.55)
        pil = ImageEnhance.Sharpness(pil).enhance(1.35)
        gray = np.array(pil)

    if numeric or profile == "numeric":
        # Numbers are usually thin and light.  Enlarge then threshold gently.
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        th = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 12
        )
        return Image.fromarray(th).convert("RGB")

    if threshold or profile in {"strong", "line_clean"}:
        block = 35 if profile != "strong" else 31
        c = 11 if profile != "strong" else 9
        gray = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, c)

    return Image.fromarray(gray).convert("RGB")


def make_preview(pil_img: Image.Image, profile: str = "auto", remove_lines: bool = True) -> Image.Image:
    """Preview image for the layout teacher screen."""
    return apply_ocr_filter(pil_img, profile=profile, numeric=False, remove_lines=remove_lines, threshold=False)
