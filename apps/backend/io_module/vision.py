"""
Vision I/O module for Echo Speak.
Handles screen capture and OCR operations.
"""

import os
import sys
from typing import Optional, Tuple
from loguru import logger

try:
    import cv2
    import numpy as np
    from PIL import Image
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("OpenCV not available")

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    logger.warning("pytesseract not available")


_TESSERACT_BINARY_OK: Optional[bool] = None

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config


def capture_screen() -> Optional[np.ndarray]:
    """
    Capture the current screen.

    Returns:
        Screen image as numpy array or None on failure.
    """
    if not CV2_AVAILABLE:
        logger.error("OpenCV not available for screen capture")
        return None

    try:
        import mss
        import mss.tools

        with mss.mss() as sct:
            monitor = sct.monitors[1]
            screenshot = sct.grab(monitor)

            img = np.array(screenshot)
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            logger.debug("Screen captured successfully")
            return img

    except Exception as e:
        logger.error(f"Screen capture failed: {e}")
        return None


def capture_region(left: int, top: int, width: int, height: int) -> Optional[np.ndarray]:
    """
    Capture a specific region of the screen.

    Args:
        left: X coordinate of left edge.
        top: Y coordinate of top edge.
        width: Width of region.
        height: Height of region.

    Returns:
        Captured region as numpy array or None on failure.
    """
    if not CV2_AVAILABLE:
        logger.error("OpenCV not available for region capture")
        return None

    try:
        import mss

        with mss.mss() as sct:
            monitor = {
                "left": left,
                "top": top,
                "width": width,
                "height": height
            }
            screenshot = sct.grab(monitor)

            img = np.array(screenshot)
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            logger.debug(f"Region captured: {left}x{top} {width}x{height}")
            return img

    except Exception as e:
        logger.error(f"Region capture failed: {e}")
        return None


def perform_ocr(image: np.ndarray, lang: str = 'eng') -> str:
    """
    Perform OCR on an image to extract text.

    Args:
        image: Input image as numpy array.
        lang: Language for OCR (default: 'eng').

    Returns:
        Extracted text as string.
    """
    global _TESSERACT_BINARY_OK

    if not TESSERACT_AVAILABLE:
        return ""

    try:
        tesseract_path = config.tesseract_path
        if tesseract_path:
            pytesseract.tesseract_cmd = tesseract_path

        if _TESSERACT_BINARY_OK is None:
            try:
                pytesseract.get_tesseract_version()
                _TESSERACT_BINARY_OK = True
            except Exception as e:
                _TESSERACT_BINARY_OK = False
                logger.warning(f"Tesseract OCR engine not available: {e}")

        if not _TESSERACT_BINARY_OK:
            return ""

        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        text = pytesseract.image_to_string(thresh, lang=lang)
        logger.debug(f"OCR extracted {len(text)} characters")
        return text.strip()

    except Exception as e:
        logger.error(f"OCR failed: {e}")
        return ""


def perform_ocr_with_boxes(image: np.ndarray, lang: str = 'eng') -> Tuple[str, list]:
    """
    Perform OCR with bounding box information.

    Args:
        image: Input image as numpy array.
        lang: Language for OCR.

    Returns:
        Tuple of (extracted text, list of bounding boxes).
    """
    global _TESSERACT_BINARY_OK

    if not TESSERACT_AVAILABLE:
        return "", []

    try:
        tesseract_path = config.tesseract_path
        if tesseract_path:
            pytesseract.tesseract_cmd = tesseract_path

        if _TESSERACT_BINARY_OK is None:
            try:
                pytesseract.get_tesseract_version()
                _TESSERACT_BINARY_OK = True
            except Exception as e:
                _TESSERACT_BINARY_OK = False
                logger.warning(f"Tesseract OCR engine not available: {e}")

        if not _TESSERACT_BINARY_OK:
            return "", []

        data = pytesseract.image_to_data(
            image,
            lang=lang,
            output_type=pytesseract.Output.DICT
        )

        text_parts = []
        boxes = []

        for i, text in enumerate(data['text']):
            if text.strip() and int(data['conf'][i]) > 30:
                text_parts.append(text)
                boxes.append({
                    'text': text,
                    'x': data['left'][i],
                    'y': data['top'][i],
                    'width': data['width'][i],
                    'height': data['height'][i],
                    'confidence': data['conf'][i]
                })

        return " ".join(text_parts), boxes

    except Exception as e:
        logger.error(f"OCR with boxes failed: {e}")
        return "", []


def save_image(image: np.ndarray, path: str) -> bool:
    """
    Save an image to disk.

    Args:
        image: Image to save.
        path: File path to save to.

    Returns:
        True on success, False on failure.
    """
    try:
        if isinstance(image, np.ndarray):
            if len(image.shape) == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(image)

        image.save(path)
        logger.info(f"Image saved to: {path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save image: {e}")
        return False


def analyze_screen_content(image: Optional[np.ndarray] = None) -> dict:
    """
    Analyze screen content and extract information.

    Args:
        image: Optional pre-captured screen image.

    Returns:
        Dictionary with analysis results.
    """
    if image is None:
        image = capture_screen()
        if image is None:
            return {"error": "Failed to capture screen"}

    ocr_text = perform_ocr(image)

    return {
        "text": ocr_text,
        "text_length": len(ocr_text),
        "has_text": bool(ocr_text.strip()),
        "image_size": {
            "width": image.shape[1],
            "height": image.shape[0]
        }
    }


def get_screen_info() -> dict:
    """
    Get information about the screen/monitor.

    Returns:
        Dictionary with screen information.
    """
    try:
        import mss
        with mss.mss() as sct:
            monitors = []
            for i, monitor in enumerate(sct.monitors[1:], 1):
                monitors.append({
                    "index": i,
                    "left": monitor["left"],
                    "top": monitor["top"],
                    "width": monitor["width"],
                    "height": monitor["height"]
                })

            return {
                "monitor_count": len(monitors),
                "monitors": monitors,
                "primary": monitors[0] if monitors else None
            }
    except Exception as e:
        logger.error(f"Failed to get screen info: {e}")
        return {"error": str(e)}


class VisionManager:
    """Manager class for vision operations."""

    def __init__(self):
        """Initialize the vision manager."""
        self.last_capture = None
        logger.info("Vision manager initialized")

    def capture_and_analyze(self) -> dict:
        """
        Capture screen and perform full analysis.

        Returns:
            Analysis results dictionary.
        """
        image = capture_screen()
        if image is None:
            return {"error": "Screen capture failed"}

        self.last_capture = image
        return analyze_screen_content(image)

    def capture_and_ocr(self) -> str:
        """
        Capture screen and extract text.

        Returns:
            Extracted text string.
        """
        image = capture_screen()
        if image is None:
            return "Failed to capture screen"

        return perform_ocr(image)

    def get_screen_info(self) -> dict:
        """Get screen information."""
        return get_screen_info()


def create_vision_manager() -> VisionManager:
    """
    Create a vision manager instance.

    Returns:
        Configured VisionManager instance.
    """
    return VisionManager()
