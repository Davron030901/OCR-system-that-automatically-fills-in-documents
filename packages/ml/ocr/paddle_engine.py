"""Local OCR via PaddleOCR, exported to ONNX for CPU inference.

Kept behind the OCREngine protocol so swapping in docTR or Tesseract is a
one-file change. If PaddleOCR is not installed the ML service logs it and runs
MRZ-only, which still produces a usable result for passports and ID cards.
"""
from __future__ import annotations

import numpy as np

from packages.ml.pipeline import OCRLine, OCROutput


class PaddleEngine:
    def __init__(self, lang: str = "cyrillic", use_gpu: bool = False):
        from paddleocr import PaddleOCR
        self._ocr = PaddleOCR(use_angle_cls=True, lang=lang, use_gpu=use_gpu,
                              show_log=False)

    def read(self, image: np.ndarray) -> OCROutput:
        raw = self._ocr.ocr(image, cls=True)
        lines: list[OCRLine] = []
        for page in raw or []:
            for entry in page or []:
                box, (text, conf) = entry[0], entry[1]
                xs = [int(p[0]) for p in box]
                ys = [int(p[1]) for p in box]
                lines.append(OCRLine(
                    text=text,
                    bbox=[min(xs), min(ys), max(xs), max(ys)],
                    confidence=float(conf),
                ))
        return OCROutput(lines=lines)
