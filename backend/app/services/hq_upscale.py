"""High-quality, CPU-only image enhancement (opt-in, slow but detailed).

Two ONNX models run on onnxruntime (no PyTorch):
  - UltraSharpV2 (4x ESRGAN) — sharp, realistic detail for the whole image
    (bikes, gear, scenes, text). Run TILED so peak memory stays low.
  - GFPGAN v1.4 — face restoration; rebuilds eyes/teeth/skin on detected faces
    and is feather-pasted back over the upscaled image.

Models are downloaded on first use and cached under storage. Everything degrades
gracefully to the fast FSRCNN upscaler (or the original bytes) on any failure, so
enabling this never breaks the gallery.

Slow by design: seconds to a couple of minutes per image on CPU. Apply it in
background work (gallery download), never inline in a render request.
"""

import logging
import os
import threading

from app.config import get_settings
from app.services.upscaler import upscale_image_bytes

logger = logging.getLogger(__name__)
settings = get_settings()

_ULTRASHARP_URL = "https://huggingface.co/Kim2091/UltraSharpV2/resolve/main/4x-UltraSharpV2_fp32_op17.onnx"
_GFPGAN_URL = "https://huggingface.co/Meeperomi/GFPGANv1.4-onnx/resolve/main/GFPGANv1.4.onnx"

_MODELS_DIR = os.path.join(settings.storage_base_path, "sr_models")
_lock = threading.Lock()
_state = {"loaded": False, "ultra": None, "gfpgan": None, "cascade": None, "failed": False}


def _download(url: str, path: str):
    if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
        return
    import urllib.request

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "ig-fb-reposter"})
    logger.info("HQ upscale: downloading %s", os.path.basename(path))
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, path)


def _get_models():
    """Lazily download + build both ONNX sessions once per process. Memory-frugal
    session options (arena off) so tiled inference stays within a tight RAM budget."""
    if _state["loaded"] or _state["failed"]:
        return _state if _state["loaded"] else None
    with _lock:
        if _state["loaded"] or _state["failed"]:
            return _state if _state["loaded"] else None
        try:
            import cv2
            import onnxruntime as ort

            ultra_path = os.path.join(_MODELS_DIR, "4x-UltraSharpV2_fp32.onnx")
            gfpgan_path = os.path.join(_MODELS_DIR, "GFPGANv1.4.onnx")
            _download(_ULTRASHARP_URL, ultra_path)
            _download(_GFPGAN_URL, gfpgan_path)

            so = ort.SessionOptions()
            so.enable_cpu_mem_arena = False
            so.enable_mem_pattern = False
            so.intra_op_num_threads = int(os.getenv("HQ_UPSCALE_THREADS", "2"))
            prov = ["CPUExecutionProvider"]
            _state["ultra"] = ort.InferenceSession(ultra_path, sess_options=so, providers=prov)
            _state["gfpgan"] = ort.InferenceSession(gfpgan_path, sess_options=so, providers=prov)
            _state["cascade"] = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
            _state["loaded"] = True
            logger.info("HQ upscale: models ready (UltraSharpV2 + GFPGAN)")
            return _state
        except Exception as exc:
            _state["failed"] = True
            logger.warning("HQ upscale disabled (model/onnxruntime unavailable): %s", exc)
            return None


def _tiled_ultrasharp(bgr, sess, tile: int, ov: int, scale: int = 4):
    import gc

    import cv2
    import numpy as np

    h, w = bgr.shape[:2]
    out = np.zeros((h * scale, w * scale, 3), np.uint8)
    name = sess.get_inputs()[0].name
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            y0, x0 = max(0, y - ov), max(0, x - ov)
            y1, x1 = min(h, y + tile + ov), min(w, x + tile + ov)
            t = bgr[y0:y1, x0:x1]
            inp = np.ascontiguousarray(
                np.transpose(cv2.cvtColor(t, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0, (2, 0, 1))[None]
            )
            o = sess.run(None, {name: inp})[0][0]
            o = cv2.cvtColor((np.clip(np.transpose(o, (1, 2, 0)), 0, 1) * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
            ty0, tx0 = (y - y0) * scale, (x - x0) * scale
            yy1, xx1 = min(h, y + tile), min(w, x + tile)
            out[y * scale:yy1 * scale, x * scale:xx1 * scale] = o[ty0:ty0 + (yy1 - y) * scale, tx0:tx0 + (xx1 - x) * scale]
            del inp, o, t
            gc.collect()
    return out


def _restore_faces(src_bgr, up_bgr, models):
    import cv2
    import numpy as np

    sh, sw = src_bgr.shape[:2]
    scale = up_bgr.shape[1] / sw
    gray = cv2.cvtColor(src_bgr, cv2.COLOR_BGR2GRAY)
    faces = models["cascade"].detectMultiScale(gray, 1.1, 5, minSize=(int(sw * 0.05), int(sh * 0.05)))
    gf = models["gfpgan"]
    name = gf.get_inputs()[0].name
    for (x, y, fw, fh) in sorted(faces, key=lambda f: -f[2] * f[3])[:2]:
        m = 0.35
        x0, y0 = max(0, int(x - m * fw)), max(0, int(y - m * fh))
        x1, y1 = min(sw, int(x + fw + m * fw)), min(sh, int(y + fh + m * fh))
        crop = src_bgr[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        f512 = cv2.resize(crop, (512, 512), interpolation=cv2.INTER_CUBIC)
        rgb = cv2.cvtColor(f512, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        inp = np.transpose((rgb - 0.5) / 0.5, (2, 0, 1))[None].astype(np.float32)
        y_out = gf.run(None, {name: inp})[0]
        y_out = y_out[0] if y_out.ndim == 4 else y_out
        rest = cv2.cvtColor((np.clip(np.transpose(y_out, (1, 2, 0)) * 0.5 + 0.5, 0, 1) * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
        ux0, uy0, ux1, uy1 = int(x0 * scale), int(y0 * scale), int(x1 * scale), int(y1 * scale)
        tw, th = ux1 - ux0, uy1 - uy0
        if tw <= 0 or th <= 0:
            continue
        rest = cv2.resize(rest, (tw, th), interpolation=cv2.INTER_CUBIC)
        mask = np.zeros((th, tw), np.float32)
        cv2.ellipse(mask, (tw // 2, th // 2), (int(tw * 0.42), int(th * 0.46)), 0, 0, 360, 1, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), tw * 0.06)[..., None]
        up_bgr[uy0:uy1, ux0:ux1] = (rest * mask + up_bgr[uy0:uy1, ux0:ux1] * (1 - mask)).astype(np.uint8)
    return up_bgr


def enhance_image_bytes(data: bytes, target_edge: int = 2048) -> bytes:
    """Return a high-detail JPEG (~target_edge long edge) using UltraSharpV2 for
    the whole image and GFPGAN for faces. Falls back to the fast FSRCNN upscaler,
    then the original bytes, on any problem. CPU-only; slow — use in background."""
    models = _get_models()
    if models is None:
        return upscale_image_bytes(data, target_edge)
    try:
        import cv2
        import numpy as np

        src = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if src is None:
            return data
        if max(src.shape[:2]) >= target_edge:
            return data

        tile = int(os.getenv("HQ_UPSCALE_TILE", "112"))
        up = _tiled_ultrasharp(src, models["ultra"], tile=tile, ov=10, scale=4)

        # trim x4 overshoot back to the target long edge
        long_edge = max(up.shape[:2])
        if long_edge > target_edge:
            s = target_edge / long_edge
            up = cv2.resize(up, (int(up.shape[1] * s), int(up.shape[0] * s)), interpolation=cv2.INTER_AREA)

        try:
            up = _restore_faces(src, up, models)
        except Exception as exc:
            logger.debug("HQ upscale: face restore skipped: %s", exc)

        ok, enc = cv2.imencode(".jpg", up, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        return enc.tobytes() if ok else data
    except Exception as exc:
        logger.warning("HQ upscale failed, falling back to FSRCNN: %s", exc)
        return upscale_image_bytes(data, target_edge)
