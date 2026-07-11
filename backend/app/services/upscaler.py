"""Gallery image upscaler — FSRCNN x2 super-resolution + light sharpening.

Small learned SR model (~39 KB) chosen over EDSR because EDSR x2 takes ~75 s per
image on CPU vs ~0.9 s for FSRCNN — far more practical on a CPU-only VPS while
still adding real detail (much better than plain Lanczos).

Only upscales small images (long edge below `_MAX_EDGE`) so already-large photos
are left untouched. Falls back to returning the original bytes if OpenCV or the
model is unavailable, so the gallery never breaks on an upscale error.
"""

import logging
import os

logger = logging.getLogger(__name__)

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "sr_models", "FSRCNN_x2.pb")
_MAX_EDGE = 1000  # skip upscaling if the long edge is already >= this
_sr = None
_sr_failed = False


def _get_sr():
    """Lazily build the DnnSuperRes model once per process."""
    global _sr, _sr_failed
    if _sr is not None or _sr_failed:
        return _sr
    try:
        import cv2
        from cv2 import dnn_superres

        sr = dnn_superres.DnnSuperResImpl_create()
        sr.readModel(_MODEL_PATH)
        sr.setModel("fsrcnn", 2)
        _sr = sr
        logger.info("Upscaler: FSRCNN x2 model loaded")
    except Exception as exc:
        _sr_failed = True
        logger.warning("Upscaler disabled (OpenCV/model unavailable): %s", exc)
    return _sr


def upscale_image_bytes(data: bytes) -> bytes:
    """Return a 2x-upscaled + sharpened JPEG, or the original bytes on any issue
    (or when the image is already large enough)."""
    sr = _get_sr()
    if sr is None:
        return data
    try:
        import cv2
        import numpy as np

        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return data
        h, w = img.shape[:2]
        if max(h, w) >= _MAX_EDGE:
            return data  # already sharp/large enough

        up = sr.upsample(img)
        # light unsharp mask for extra crispness
        blur = cv2.GaussianBlur(up, (0, 0), 1.2)
        up = cv2.addWeighted(up, 1.4, blur, -0.4, 0)

        ok, enc = cv2.imencode(".jpg", up, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        if not ok:
            return data
        return enc.tobytes()
    except Exception as exc:
        logger.warning("Upscale failed, using original: %s", exc)
        return data
