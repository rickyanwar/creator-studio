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
# Target long edge — Getty originals are locked at ~612px, so we upscale toward
# ~2K for crisp detail in the 2× design render. Repeated x2 passes (612→1224→2448)
# reach it; overshoot is downscaled back to the target.
_TARGET_EDGE = int(os.getenv("GALLERY_UPSCALE_TARGET", "2048"))
_MAX_PASSES = 2
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


def upscale_image_bytes(data: bytes, target_edge: int | None = None) -> bytes:
    """Return an upscaled + sharpened JPEG whose long edge is ~`target_edge`
    (default ~2K), or the original bytes on any issue (or when it is already big
    enough). Applies repeated FSRCNN x2 passes, then trims any overshoot."""
    target = target_edge or _TARGET_EDGE
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
        if max(img.shape[:2]) >= target:
            return data  # already sharp/large enough

        up = img
        passes = 0
        while max(up.shape[:2]) < target and passes < _MAX_PASSES:
            up = sr.upsample(up)
            passes += 1
        if passes == 0:
            return data

        # Trim big overshoot back to the target (keeps file size sane).
        long_edge = max(up.shape[:2])
        if long_edge > target * 1.25:
            s = target / long_edge
            up = cv2.resize(up, (int(up.shape[1] * s), int(up.shape[0] * s)), interpolation=cv2.INTER_AREA)

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
