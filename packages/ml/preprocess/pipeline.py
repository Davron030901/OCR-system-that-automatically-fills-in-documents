"""Image preprocessing.

Roughly 60% of end-to-end accuracy is decided here rather than in any model.
A rectified, evenly-lit, correctly-scaled crop makes a mediocre recogniser
look good; a skewed glare-covered photo defeats a state-of-the-art one.

The other job of this module is refusing work. Telling a user "the document
number is under glare, move away from the light" costs nothing and produces a
usable second photo. Running OCR on that image produces a confident wrong
answer, which is far more expensive.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from packages.schema.models import QualityReport

# ID-1 (national ID card) is 85.6 x 54 mm. At 300 DPI that is 1012 x 638 px.
ID1_SIZE = (1012, 638)
ID1_RATIO = 85.6 / 54.0          # 1.585
PASSPORT_RATIO = 1.42

BLUR_MIN = 60.0                  # Laplacian variance below this is unusable
DPI_MIN = 200


@dataclass
class PreprocessConfig:
    detect_document: bool = True
    rectify: bool = True
    deskew: bool = True
    illumination: bool = True
    denoise: bool = True
    sharpen: bool = True
    upscale: bool = True
    target_size: tuple[int, int] | None = None


@dataclass
class PreprocessResult:
    rectified: np.ndarray
    mrz_ready: np.ndarray | None
    quality: QualityReport
    transform: np.ndarray | None = None
    applied_steps: list[str] = field(default_factory=list)
    ms: int = 0

    def to_original(self, bbox: list[int]) -> list[int]:
        """Map a box from rectified space back to the uploaded image.

        Without this the review UI cannot draw boxes over the photo the user
        actually took, and the whole field-to-image linking breaks.
        """
        if self.transform is None:
            return bbox
        inv = np.linalg.inv(self.transform)
        x1, y1, x2, y2 = bbox
        pts = np.array([[[x1, y1]], [[x2, y1]], [[x2, y2]], [[x1, y2]]],
                       dtype=np.float32)
        mapped = cv2.perspectiveTransform(pts, inv).reshape(-1, 2)
        return [int(mapped[:, 0].min()), int(mapped[:, 1].min()),
                int(mapped[:, 0].max()), int(mapped[:, 1].max())]


# --------------------------------------------------------------------------
# Quality assessment
# --------------------------------------------------------------------------


def blur_score(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def find_glare(gray: np.ndarray, thresh: int = 250,
               min_area: int = 400) -> list[list[int]]:
    """Saturated regions large enough to hide a field."""
    _, mask = cv2.threshold(gray, thresh, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        if cv2.contourArea(c) >= min_area:
            x, y, w, h = cv2.boundingRect(c)
            out.append([x, y, x + w, y + h])
    return out


def detect_moire(gray: np.ndarray) -> bool:
    """Periodic frequency peaks indicate a photo of a screen, not a document."""
    small = cv2.resize(gray, (256, 256))
    spectrum = np.abs(np.fft.fftshift(np.fft.fft2(small.astype(np.float32))))
    spectrum = np.log1p(spectrum)
    h, w = spectrum.shape
    spectrum[h // 2 - 8:h // 2 + 8, w // 2 - 8:w // 2 + 8] = 0   # drop DC
    peak, mean = spectrum.max(), spectrum.mean()
    return bool(mean > 0 and peak / mean > 3.2)


def assess(image: np.ndarray) -> QualityReport:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    h, w = gray.shape[:2]

    report = QualityReport(
        blur_score=blur_score(gray),
        brightness=float(gray.mean()),
        contrast=float(gray.std()),
        estimated_dpi=int(w / (85.6 / 25.4)),      # assume a full-width ID-1
        glare_regions=find_glare(gray),
        moire_detected=detect_moire(gray),
    )

    reasons: list[str] = []
    if report.blur_score < BLUR_MIN:
        reasons.append("Rasm juda xira — hujjatni tekis qo'yib, "
                       "yorug'roq joyda qayta suratga oling")
    if report.brightness < 60:
        reasons.append("Rasm juda qorong'i — yorug'roq joyda suratga oling")
    elif report.brightness > 215:
        reasons.append("Rasm juda yorqin — to'g'ridan-to'g'ri yorug'likdan "
                       "chetlanib suratga oling")
    if report.contrast < 25:
        reasons.append("Rasm kontrasti past — fon bilan hujjat ajralmayapti")
    total_glare = sum((b[2] - b[0]) * (b[3] - b[1]) for b in report.glare_regions)
    if total_glare > 0.06 * h * w:
        reasons.append("Hujjat ustida yorqin dog' bor — matnni yopib qo'yishi "
                       "mumkin, boshqa burchakdan suratga oling")
    if report.estimated_dpi < DPI_MIN:
        reasons.append("Rasm o'lchami kichik — hujjatni kadrga to'liq "
                       "sig'diring va yaqinroqdan oling")
    if report.moire_detected:
        reasons.append("Ekrandan olingan surat aniqlandi — asl hujjatni "
                       "suratga oling")

    report.reasons = reasons
    report.is_acceptable = not reasons
    return report


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


def order_corners(pts: np.ndarray) -> np.ndarray:
    """Order four points as top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0], rect[2] = pts[np.argmin(s)], pts[np.argmax(s)]
    d = np.diff(pts, axis=1).ravel()
    rect[1], rect[3] = pts[np.argmin(d)], pts[np.argmax(d)]
    return rect


def find_document(image: np.ndarray) -> np.ndarray | None:
    """Locate the document outline, returning four ordered corners."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    h, w = gray.shape[:2]

    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 40, 140)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:6]:
        area = cv2.contourArea(c)
        if area < 0.15 * h * w:              # too small to be the document
            continue
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) == 4:
            corners = order_corners(approx.reshape(4, 2).astype(np.float32))
            wa = np.linalg.norm(corners[1] - corners[0])
            ha = np.linalg.norm(corners[3] - corners[0])
            if ha > 0:
                ratio = max(wa / ha, ha / wa)
                if 1.2 < ratio < 1.9:        # plausible ID or passport page
                    return corners
    return None


def four_point_transform(image: np.ndarray, corners: np.ndarray,
                         size: tuple[int, int] | None = None
                         ) -> tuple[np.ndarray, np.ndarray]:
    tl, tr, br, bl = corners
    width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    if size:
        width, height = size
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1],
                    [0, height - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(corners, dst)
    return cv2.warpPerspective(image, matrix, (width, height)), matrix


def deskew(image: np.ndarray, max_angle: float = 15.0) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 100,
                            minLineLength=gray.shape[1] // 3, maxLineGap=20)
    if lines is None:
        return image
    angles = []
    for x1, y1, x2, y2 in lines[:, 0]:
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(angle) < max_angle:
            angles.append(angle)
    if not angles:
        return image
    angle = float(np.median(angles))
    if abs(angle) < 0.3:
        return image
    h, w = image.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(image, m, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


# --------------------------------------------------------------------------
# Enhancement
# --------------------------------------------------------------------------


def correct_illumination(image: np.ndarray) -> np.ndarray:
    """Flatten uneven lighting by dividing out an estimated background."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41))
    background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
    flat = cv2.divide(gray, background, scale=255)
    return cv2.cvtColor(flat, cv2.COLOR_GRAY2BGR) if image.ndim == 3 else flat


def enhance(image: np.ndarray, cfg: PreprocessConfig) -> tuple[np.ndarray, list[str]]:
    steps: list[str] = []
    out = image

    if cfg.illumination:
        out = correct_illumination(out)
        steps.append("illumination")

    lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB) if out.ndim == 3 else None
    if lab is not None:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        steps.append("clahe")

    if cfg.denoise:
        out = cv2.bilateralFilter(out, 5, 50, 50)
        steps.append("denoise")

    if cfg.sharpen:
        blur = cv2.GaussianBlur(out, (0, 0), 2.0)
        out = cv2.addWeighted(out, 1.5, blur, -0.5, 0)
        steps.append("sharpen")

    return out, steps


def binarize_for_mrz(image: np.ndarray) -> np.ndarray:
    """Aggressive binarisation, appropriate ONLY for the MRZ strip."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY, 31, 10)


def preprocess(image: np.ndarray,
               cfg: PreprocessConfig | None = None) -> PreprocessResult:
    """Full pipeline: assess, rectify, enhance, and keep the transform."""
    cfg = cfg or PreprocessConfig()
    started = time.time()
    steps: list[str] = []
    transform = None
    work = image

    if cfg.detect_document:
        corners = find_document(image)
        if corners is not None and cfg.rectify:
            work, transform = four_point_transform(image, corners,
                                                   cfg.target_size)
            steps.append("rectify")

    if cfg.upscale and work.shape[1] < 900:
        scale = 900 / work.shape[1]
        work = cv2.resize(work, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_LANCZOS4)
        steps.append("upscale")

    if cfg.deskew:
        work = deskew(work)
        steps.append("deskew")

    quality = assess(work)
    enhanced, esteps = enhance(work, cfg)
    steps += esteps

    return PreprocessResult(
        rectified=enhanced,
        mrz_ready=binarize_for_mrz(enhanced),
        quality=quality,
        transform=transform,
        applied_steps=steps,
        ms=int((time.time() - started) * 1000),
    )
