import json
import logging
from dataclasses import dataclass, field
from typing import Any
import re

from app.services.design_images import _vision_chat, _vision_datauri

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class QAResult:
    passed: bool
    problems: tuple[str, ...]
    scores: dict[str, Any] = field(default_factory=dict)
    ocr_text: str = ""

def normalise_text(s: str) -> str:
    s = s.upper()
    s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    s = s.replace("…", "...")
    s = re.sub(r'[^A-Z0-9\!\?\.\,\'\"\ ]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def radio_text_matches(ocr: str, header: tuple[str, ...], lines: tuple[str, ...]) -> str | None:
    ocr_norm = normalise_text(ocr)
    
    for token in header:
        token_norm = normalise_text(token)
        if token_norm and token_norm not in ocr_norm:
            return token
    
    for line in lines:
        line_norm = normalise_text(line)
        if line_norm and line_norm not in ocr_norm:
            return line
            
    return None

def parse_qa_json(raw: str) -> dict[str, Any]:
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0]
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0]
    return json.loads(raw.strip())

def evaluate(qa_dict: dict, layout: str, ocr_mismatch: str | None, inset_modes: tuple[str, ...] = (), whatif_target_team: str | None = None) -> tuple[bool, tuple[str, ...]]:
    problems = []
    
    if qa_dict.get("identity_hero") is False:
        problems.append("identity_changed")
        
    if whatif_target_team:
        if qa_dict.get("identity_inset") is False:
            problems.append("identity_changed")
        if qa_dict.get("plain_whatif_suit"):
            problems.append("plain_whatif_suit")
    else:
        # non-whatif inset identity
        real_inset = False
        if not inset_modes and qa_dict.get("identity_inset") is False:
            problems.append("identity_changed")
        if inset_modes:
            for m in inset_modes:
                if m == 'real' and qa_dict.get("identity_inset") is False:
                    problems.append("identity_changed")
                if m in ('asset', 'imagined') and qa_dict.get("inset_shows_hero"):
                    problems.append("inset_shows_hero")
                    
    if layout != "team_radio" and qa_dict.get("text_on_image"):
        problems.append("text_present")
        
    if qa_dict.get("source_branding_visible"):
        problems.append("source_branding")
        
    if qa_dict.get("ai_look", 1) >= 4:
        problems.append("looks_ai")
        
    if layout != "team_radio" and not qa_dict.get("bottom_area_empty", True):
        problems.append("bottom_not_empty")
        
    if layout == "team_radio" and ocr_mismatch is not None:
        problems.append(f"radio_text_mismatch:{ocr_mismatch}")
        
    return len(problems) == 0, tuple(problems)

def check_generated(
    image_bytes: bytes,
    *,
    layout: str,
    hero_ref_path: str,
    inset_ref_paths: tuple[str, ...] = (),
    inset_modes: tuple[str, ...] = (),
    expected_lines: tuple[str, ...] = (),
    expected_header: tuple[str, ...] = (),
    source_branding: tuple[str, ...] = (),
    whatif_target_team: str | None = None
) -> QAResult:
    try:
        content = [
            {"type": "text", "text": f"This is a generated image for a '{layout}' layout. Evaluate it against the reference images."}
        ]
        
        content.append({
            "type": "image_url", 
            "image_url": {"url": _vision_datauri(image_bytes)}
        })
        
        with open(hero_ref_path, "rb") as f:
            hero_bytes = f.read()
        content.append({"type": "text", "text": "Reference image for HERO (person/car):"})
        content.append({
            "type": "image_url",
            "image_url": {"url": _vision_datauri(hero_bytes)}
        })
        

            
        for i, ref in enumerate(inset_ref_paths):
            with open(ref, "rb") as f:
                inset_bytes = f.read()
            content.append({"type": "text", "text": f"Reference image for INSET {i+1}:"})
            content.append({
                "type": "image_url",
                "image_url": {"url": _vision_datauri(inset_bytes)}
            })

        branding_str = ", ".join(source_branding) if source_branding else "any watermarks or handles"
        whatif_clause = ""
        plain_suit_line = ""
        inset_shows_hero_line = ""
        
        if whatif_target_team:
            whatif_clause = f'\nNote: This is a "what-if" transfer graphic to {whatif_target_team}. The inset portrait is SUPPOSED to wear the {whatif_target_team} uniform. The {whatif_target_team} logos on the inset clothing are INTENTIONAL. Do NOT list {whatif_target_team} logos as invented/garbled. Do NOT list the hero\'s original logos as missing from the inset.'
            plain_suit_line = f'\n- "plain_whatif_suit": bool (is the what-if {whatif_target_team} suit plain/sponsorless, missing the target team\'s main sponsor logos shown in the kit reference?)'
        else:
            if inset_modes and any(m in ('asset', 'imagined') for m in inset_modes):
                inset_shows_hero_line = f'\n- "inset_shows_hero": bool (does the inset image show the hero person?)'

        prompt = f"""
Return STRICT JSON ONLY, no markdown formatting. The JSON must have these exact keys:
- "identity_hero": bool (is the main person/subject the exact same real person as in the HERO reference?)
- "identity_inset": bool or null (if there is a real person inset, are they the exact same real person as in the INSET reference? null if no inset){whatif_clause}{plain_suit_line}{inset_shows_hero_line}
- "invented_or_garbled_logos": list of strings (logos/text on clothing/cars that look invented, misspelled, or melted. Ignore {whatif_target_team or 'nothing'} logos in the inset if what-if. Small garbled text is allowed, list it here)
- "missing_real_logos": list of strings (clearly visible logos on the reference clothing/car that disappeared. Ignore missing hero logos on the inset if what-if)
- "text_on_image": list of strings (any letters/words/numbers rendered on the image OUTSIDE of clothing/car liveries)
- "source_branding_visible": bool (Does the image contain the source account's logo/watermark/branding, e.g. {branding_str}, or any other watermark/overlay?)
- "ai_look": integer (1 = natural professional photo, to 5 = obviously AI/over-processed/plastic)
- "bottom_area_empty": bool (is the bottom 35% of the image empty of faces, bodies, objects, and text?)

{{
    "identity_hero": true,
    "identity_inset": null,
    "invented_or_garbled_logos": [],
    "missing_real_logos": [],
    "text_on_image": [],
    "source_branding_visible": false,
    "ai_look": 1,
    "bottom_area_empty": true
}}
"""
        content.append({"type": "text", "text": prompt})
        
        raw_qa = _vision_chat(content, max_tokens=500, temperature=0, context="visual_qa")
        qa_dict = parse_qa_json(raw_qa)
        
        ocr_text = ""
        ocr_mismatch = None
        
        if layout == "team_radio":
            ocr_content = [
                {"type": "text", "text": "Transcribe ALL text visible in this image exactly as written. Pay attention to quotes, punctuation, and capitalisation."},
                {"type": "image_url", "image_url": {"url": _vision_datauri(image_bytes)}}
            ]
            ocr_text = _vision_chat(ocr_content, max_tokens=800, temperature=0, context="visual_qa_ocr")
            ocr_mismatch = radio_text_matches(ocr_text, expected_header, expected_lines)
            
        passed, problems = evaluate(qa_dict, layout, ocr_mismatch, inset_modes, whatif_target_team)
        return QAResult(passed=passed, problems=problems, scores=qa_dict, ocr_text=ocr_text)
        
    except Exception as e:
        logger.warning(f"Vision QA failed: {e}")
        return QAResult(passed=False, problems=("qa_unavailable",))
