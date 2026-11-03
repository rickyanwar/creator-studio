"""Edit the original Instagram photo without a card or aesthetic QA pass."""

import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from app.config import get_settings
from app.services.identity_check import check_identity
from app.services.speaker_design import face_info
from app.services.visual_engine import GenerationRequest, VisualEngineDeferred, generate_ex


EDIT_RULES = (
    "Keep every real person's face, identity, age and expression exactly the same; "
    "keep natural skin texture and film grain, no beauty smoothing; "
    "no over-sharpening and no HDR look on cars and motorcycles; "
    "keep all real logos, sponsor marks and team names exactly as they are, never invent new text or logos; "
    "keep the same people, vehicles and objects unless the owner instructions say otherwise; "
    "photorealistic, same camera perspective; vertical 4:5 output; "
    "do not add borders, captions, watermarks or frames."
)
OUTPUT_SIZE = (1080, 1350)


def build_edit_prompt(owner_prompt: str, *, subject: str | None) -> str:
    return f"Edit this exact photo. Owner instructions: {owner_prompt}\n\n{EDIT_RULES}"


def clean_jpeg(src_path: str, out_path: str) -> str:
    with Image.open(src_path) as src:
        image = ImageOps.exif_transpose(src).convert("RGB")
        image = ImageOps.fit(image, OUTPUT_SIZE, method=Image.Resampling.LANCZOS)
        image.info.clear()
        image.save(out_path, "JPEG", quality=95, optimize=True)
    return out_path


def edit_ig_photo(db, *, photo_path: str, owner_prompt: str, fanpage_id: int,
                  subject: str | None, qa_enabled: bool) -> dict:
    engine = None
    try:
        req = GenerationRequest(
            layout="solo", refs=(photo_path,),
            prompt=build_edit_prompt(owner_prompt, subject=subject),
        )
        res = generate_ex(db, req, timeout=300, fanpage_id=fanpage_id)
        engine = "chatgpt" if res.engine == "gptcli" else (
            "codex_fallback" if res.meta.get("fallback_from") == "gptcli" else "codex"
        )
        generated = res.image
        if not generated:
            return {"ok": False, "image_path": None, "engine": engine, "reason": "empty output"}
        if qa_enabled:
            original = Path(photo_path).read_bytes()
            if face_info(original) is not None:
                identity = check_identity(
                    original, generated, subject=subject or "the people in the original photo",
                    context="ig_edit", fanpage_id=fanpage_id,
                )
                if not identity.passed:
                    return {"ok": False, "image_path": None, "engine": engine, "reason": identity.reason}

        designs = Path(get_settings().storage_base_path) / "designs"
        designs.mkdir(parents=True, exist_ok=True)
        final = str(designs / f"ig_edit_{uuid.uuid4().hex[:8]}.jpg")
        with io.BytesIO(generated) as source:
            clean_jpeg(source, final)
        return {"ok": True, "image_path": final, "engine": engine, "reason": ""}
    except VisualEngineDeferred:
        raise
    except Exception as exc:
        return {"ok": False, "image_path": None, "engine": engine, "reason": str(exc)}
