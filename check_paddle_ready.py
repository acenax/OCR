
from __future__ import annotations

import sys

print("Python", sys.version)
try:
    import numpy
    print("numpy", numpy.__version__)
except Exception as exc:
    print("numpy ERROR", exc)

try:
    import cv2
    print("cv2", cv2.__version__)
except Exception as exc:
    print("cv2 ERROR", exc)

try:
    import paddle
    paddle.utils.run_check()
except Exception as exc:
    print("paddle ERROR", exc)

try:
    from paddleocr import PaddleOCR
    print("PaddleOCR import OK")
    ocr = PaddleOCR(lang="en", use_angle_cls=True, use_gpu=False, show_log=False)
    print("PaddleOCR init OK")
except Exception as exc:
    print("PaddleOCR ERROR", exc)
    raise
