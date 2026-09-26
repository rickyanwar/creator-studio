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
# OpenCV's FSRCNN allocates full-resolution float feature maps, so memory
# grows with the image AREA: measured 2026-09-26, a 720x927 photo needed
# +0.86 GB for pass 1 and +2.7 GB for pass 2 (1440x1854 → 2880x3708) — the
# Celery processes the VPS kept OOM-killing (2.1-2.7 GB), taking Mode 7
# renders down with them. Upsampling in tiles bounds that to one tile's worth.
# The overlap (per side) is wider than FSRCNN's receptive field, so each
# tile's core is identical to an untiled pass and no seams appear.
_TILE = 256
_TILE_OVERLAP = 16
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


def _upsample_tiled(sr, img):
    """One FSRCNN x2 pass, tile by tile (see _TILE). Returns a 2x image."""
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    if h * w <= _TILE * _TILE:
        return sr.upsample(img)
    out = np.empty((h * 2, w * 2, img.shape[2]), dtype=img.dtype)
    for y0 in range(0, h, _TILE):
        for x0 in range(0, w, _TILE):
            y1, x1 = min(y0 + _TILE, h), min(x0 + _TILE, w)
            py0, px0 = max(0, y0 - _TILE_OVERLAP), max(0, x0 - _TILE_OVERLAP)
            py1, px1 = min(h, y1 + _TILE_OVERLAP), min(w, x1 + _TILE_OVERLAP)
            up = sr.upsample(np.ascontiguousarray(img[py0:py1, px0:px1]))
            want_h, want_w = (py1 - py0) * 2, (px1 - px0) * 2
            if up.shape[:2] != (want_h, want_w):
                up = cv2.resize(up, (want_w, want_h), interpolation=cv2.INTER_CUBIC)
            cy, cx = (y0 - py0) * 2, (x0 - px0) * 2
            out[y0 * 2:y1 * 2, x0 * 2:x1 * 2] = up[cy:cy + (y1 - y0) * 2, cx:cx + (x1 - x0) * 2]
    return out


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
            up = _upsample_tiled(sr, up)
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
