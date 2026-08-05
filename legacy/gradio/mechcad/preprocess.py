from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps


def preprocess_image(input_path: Path, run_dir: Path) -> tuple[Path, dict[str, Any]]:
    """Clean a sketch image and collect light-weight geometry hints."""
    output_path = run_dir / "processed.png"
    mask_path = run_dir / "line_mask.png"

    try:
        import cv2

        image = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("OpenCV failed to read image.")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # Upscale small drawings so dimension text survives compression and VLM resizing.
        height, width = gray.shape[:2]
        min_side = min(width, height)
        scale = 1.0
        if min_side < 1400:
            scale = 1400 / float(min_side)
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        contrast = clahe.apply(gray)
        blur = cv2.GaussianBlur(contrast, (0, 0), 1.0)
        sharpened = cv2.addWeighted(contrast, 1.55, blur, -0.55, 0)
        enhanced = cv2.normalize(sharpened, None, 0, 255, cv2.NORM_MINMAX)

        adaptive = cv2.adaptiveThreshold(
            enhanced,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            31,
            12,
        )
        _, otsu = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        edges = cv2.Canny(cv2.GaussianBlur(enhanced, (3, 3), 0), 40, 130)
        combined = cv2.bitwise_or(cv2.bitwise_or(adaptive, otsu), edges)
        kernel = np.ones((2, 2), np.uint8)
        combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel, iterations=1)

        vision_image = cv2.bitwise_not(combined)
        cv2.imwrite(str(output_path), vision_image)
        cv2.imwrite(str(mask_path), combined)

        contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        areas = [float(cv2.contourArea(c)) for c in contours if cv2.contourArea(c) > 20]
        return output_path, {
            "width": int(image.shape[1]),
            "height": int(image.shape[0]),
            "processed_width": int(vision_image.shape[1]),
            "processed_height": int(vision_image.shape[0]),
            "preprocess_scale": float(scale),
            "contour_count": len(areas),
            "ink_ratio": float(np.count_nonzero(combined) / combined.size),
            "largest_contours": sorted(areas, reverse=True)[:5],
            "preprocess_variant": "white_background_enhanced_line_art",
        }
    except Exception:
        pil_image = Image.open(input_path).convert("L")
        pil_image = ImageOps.autocontrast(pil_image)
        pil_image.save(output_path)
        arr = np.asarray(pil_image)
        return output_path, {
            "width": pil_image.width,
            "height": pil_image.height,
            "contour_count": 0,
            "ink_ratio": float(np.mean(arr < 220)),
            "largest_contours": [],
        }
