"""Image preprocessing filters for OCR."""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


def pil_to_rgb_array(image: Image.Image) -> np.ndarray:
    return np.array(image.convert("RGB"))


def remove_colored_lines(rgb: np.ndarray) -> np.ndarray:
    """Remove high-saturation coloured guide/scan lines while keeping black text.

    This targets red/green/blue rainbow lines that often cross the table rows.
    Black/gray text has low saturation and is preserved.
    """
    if rgb.ndim != 3:
        return rgb
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = cv2.split(hsv)
    mask = (s > 55) & (v > 80)
    # Dilate horizontally to cover thin coloured strokes.
    mask_u8 = (mask.astype(np.uint8) * 255)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 1))
    mask_u8 = cv2.dilate(mask_u8, kernel, iterations=1)
    out = rgb.copy()
    out[mask_u8 > 0] = [255, 255, 255]
    return out


def deskew_gray(gray: np.ndarray) -> np.ndarray:
    try:
        coords = np.column_stack(np.where(gray < 245))
        if coords.size == 0:
            return gray
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
        if abs(angle) < 0.15 or abs(angle) > 4:
            return gray
        h, w = gray.shape[:2]
        center = (w // 2, h // 2)
        mat = cv2.getRotationMatrix2D(center, angle, 1.0)
        return cv2.warpAffine(gray, mat, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    except Exception:
        return gray


def enhance_for_ocr(image: Image.Image | np.ndarray, mode: str = "auto", remove_color_lines: bool = True) -> np.ndarray:
    """Return a high-contrast binary/gray image for Tesseract."""
    if isinstance(image, Image.Image):
        rgb = pil_to_rgb_array(image)
    else:
        rgb = image.copy()
        if rgb.ndim == 2:
            rgb = cv2.cvtColor(rgb, cv2.COLOR_GRAY2RGB)

    if remove_color_lines or mode in {"remove_color_lines", "faint", "numeric"}:
        rgb = remove_colored_lines(rgb)

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = deskew_gray(gray)

    # Upscale small crops so Tesseract has enough pixels.
    h, w = gray.shape[:2]
    if min(h, w) < 80:
        gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)

    gray = cv2.bilateralFilter(gray, 5, 35, 35)
    clahe = cv2.createCLAHE(clipLimit=2.0 if mode != "strong" else 3.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    if mode in {"none", "raw"}:
        return gray

    block = 31 if mode != "strong" else 21
    c = 10 if mode != "strong" else 7
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, c)
    return th


def remove_table_gridlines(image: Image.Image | np.ndarray) -> np.ndarray:
    """Compatibility wrapper used by older code."""
    if isinstance(image, Image.Image):
        rgb = pil_to_rgb_array(image)
    else:
        rgb = image.copy()
    if rgb.ndim == 2:
        rgb = cv2.cvtColor(rgb, cv2.COLOR_GRAY2RGB)
    return remove_colored_lines(rgb)
