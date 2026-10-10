from typing import List, Tuple
import base64
import io
from dataclasses import dataclass
from typing import Literal
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from PIL import Image
import httpx

from sqlalchemy.orm import Session

from app.models.settings import Settings
from app.models.ai_copy_events import AICopyEvent
from app.services.nine_router import get_nine_router_config
from app.services.visual_style import treatment_clause

class VisualEngineError(Exception):
    def __init__(self, kind: Literal['quota', 'auth', 'timeout', 'bad_response', 'transport'], message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class GenerationRequest:
    layout: Literal['inset', 'solo', 'action', 'team_radio']
    refs: tuple[str, ...]
    prompt: str
    size: str = '1024x1280'
    quality: str = 'high'
    model: str = 'cx/gpt-image-2.5'


def build_inset_prompt(
    hero_desc: str,
    insets: List[dict],
    purpose: str,
    accent: str,
    hero_path: str,
    sport: str = 'Formula 1',
    treatment: str = 'clean'
) -> Tuple[str, List[str]]:
    refs = [hero_path]
    prompt_lines = [
        f"Design a premium vertical (4:5) {sport} social media graphic of the quality a senior graphic designer at the best {sport} media pages produces in Photoshop. Use only the REAL people and objects from the reference photos — never invent, beautify or alter a face.\n",
        f"HERO (Image 1): {hero_desc}. Professional cut-out with clean hair edges. Crop tight and bold: top of the head about 8-12% from the top edge, shoulders filling the card width, face large (about 40-45% of the card width), placed center-right, eyes in the upper third. Keep his real face, identity, expression, skin texture, hair, clothing and every logo, patch, cap badge and microphone flag exactly as in image 1 — sharp, legible, nothing redrawn, removed or invented.\n"
    ]
    
    idx = 2
    inset_instructions = []
    
    for i, inst in enumerate(insets):
        mode = inst['mode']
        desc = inst['desc']
        if mode == 'real':
            refs.append(inst['path'])
            inset_instructions.append(f"INSET {i+1} (Image {idx}): REAL PHOTO. {desc}. Keep the real identity, clothing, and logos exactly as in Image {idx}.")
            idx += 1
        elif mode == 'asset':
            refs.append(inst['path'])
            inset_instructions.append(f"INSET {i+1} (Image {idx}): ASSET SCENE. {desc}. Use Image {idx} as a reference (e.g., for the exact team logo or car) and compose it into an imagined premium scene. Never alter or fake the real hero. The inset must NOT show the hero person. No text, letters or numbers anywhere. Any team logo must follow Image {idx}.")
            idx += 1
        elif mode == 'imagined':
            inset_instructions.append(f"INSET {i+1}: FULLY AI-IMAGINED SCENE. {desc}. Create a premium sports-media scene. Never alter or fake the real hero. The inset must NOT show the hero person. No text, letters or numbers anywhere. Keep the premium sports-media look (no cheap clip-art, no money rain).")
    
    if len(insets) == 1:
        comp_desc = "Crop it tight on its subject and place it inside a large circle (about 45% of the card width) with a clean solid white ring (thin but strong) and a soft drop shadow, upper-left. The hero overlaps the right edge of the circle (hero in front) with a soft contact shadow on the circle — real depth, like layered Photoshop layers."
    else:
        comp_desc = "Crop them tight on their subjects and place them inside TWO circles. Use different sizes (e.g. a large circle upper-left and a smaller one overlapping it lower-left/mid-left, or one upper-left + one upper-right with the hero centred), both with the same clean white ring (thin but strong) and a soft drop shadow. The hero overlaps at least one circle (hero in front) with a soft contact shadow — real depth. Everything inside the top 65%."
        
    prompt_lines.append("\n".join(inset_instructions) + f"\n{comp_desc}\n")
    
    prompt_lines.append(f"{treatment_clause(treatment, accent)}\n")
    prompt_lines.append(
        f"GRADE & LIGHT: one consistent, cinematic but natural sports-media colour grade across hero, inset and background; high micro-contrast on the face, rich true team colours, soft cool rim light on the hero's edge, clean separation from the background. Looks like a real photograph composited by a professional, not AI art.\n"
    )
    prompt_lines.append(
        f"Bottom 35% MUST be a smooth dark gradient with no faces, no bodies, no objects, completely empty (a headline is added later). Do NOT add any text, letters, numbers, quote marks, badges, page logos, watermarks, glow, neon, lens flares or light streaks."
    )

    return ("\n".join(prompt_lines), refs)


def build_solo_prompt(hero_desc: str, purpose: str, accent: str, sport: str = 'Formula 1', treatment: str = 'clean') -> str:
    return (
        f"Design a premium vertical (4:5) {sport} social media graphic of the quality a senior graphic designer at the best {sport} media pages produces in Photoshop. Use only the REAL people and objects from the reference photos — never invent, beautify or alter a face.\n\n"
        f"HERO (image 1): {hero_desc}. Professional cut-out with clean hair edges. Crop tight and bold: top of the head about 8-12% from the top edge, shoulders filling the card width, face large (about 40-45% of the card width), placed center, eyes in the upper third. Keep his real face, identity, expression, skin texture, hair, clothing and every logo, patch, cap badge and microphone flag exactly as in image 1 — sharp, legible, nothing redrawn, removed or invented.\n\n"
        f"{treatment_clause(treatment, accent)}\n\n"
        f"GRADE & LIGHT: one consistent, cinematic but natural sports-media colour grade across hero and background; high micro-contrast on the face, rich true team colours, soft cool rim light on the hero's edge, clean separation from the background. Looks like a real photograph composited by a professional, not AI art.\n\n"
        f"Bottom 35% MUST be a smooth dark gradient with no faces, no bodies, no objects, completely empty (a headline is added later). Do NOT add any text, letters, numbers, quote marks, badges, page logos, watermarks, glow, neon, lens flares or light streaks."
    )


def build_action_prompt(subject_desc: str, purpose: str, accent: str, sport: str = 'Formula 1', treatment: str = 'clean') -> str:
    return (
        f"Design a premium vertical (4:5) {sport} social media graphic of the quality a senior graphic designer at the best {sport} media pages produces in Photoshop. Use only the REAL people and objects from the reference photos — never invent or alter.\n\n"
        f"HERO (image 1): {subject_desc}. One big action photo. Crop tight and bold. Keep the real identity, clothing and every logo exactly as in image 1 — sharp, legible, nothing redrawn, removed or invented.\n\n"
        f"{treatment_clause(treatment, accent)}\n\n"
        f"GRADE & LIGHT: one consistent, cinematic but natural sports-media colour grade; rich true team colours. Looks like a real photograph composited by a professional, not AI art.\n\n"
        f"Bottom 35% MUST be a smooth dark gradient with no faces, no bodies, no objects, completely empty (a headline is added later). Do NOT add any text, letters, numbers, quote marks, badges, page logos, watermarks, glow, neon, lens flares or light streaks."
    )



def build_whatif_inset_prompt(
    hero_desc: str,
    target_team: str,
    target_team_color: str,
    target_team_logo_desc: str,
    accent: str,
    hero_path: str,
    alt_path: str | None,
    kit_path: str | None,
    sport: str = 'Formula 1',
    treatment: str = 'clean'
) -> Tuple[str, List[str]]:
    refs = [hero_path]
    prompt_lines = [
        f"Design a premium vertical (4:5) {sport} social media graphic of the quality a senior graphic designer at the best {sport} media pages produces in Photoshop. Use only the REAL people and objects from the reference photos — never invent, beautify or alter a face.\n",
        f"HERO (Image 1): {hero_desc} (real, keep exactly). Professional cut-out with clean hair edges. Crop tight and bold: top of the head about 8-12% from the top edge, shoulders filling the card width, face large (about 40-45% of the card width), placed center-right, eyes in the upper third. Keep his real face, identity, expression, skin texture, hair, clothing and every logo, patch, cap badge and microphone flag exactly as in image 1 — sharp, legible, nothing redrawn, removed or invented.\n"
    ]
    
    idx = 2
    if alt_path:
        refs.append(alt_path)
        alt_str = f"Image {idx} = ALTERNATE POSE of the same person (use this face/pose for the circle)"
        idx += 1
    else:
        alt_str = f"use the HERO face from Image 1 but with a clearly different head angle"
        
    if kit_path:
        refs.append(kit_path)
        kit_str = f"Image {idx} = KIT REFERENCE (copy the suit + cap design and EVERY sponsor logo/patch exactly; ignore this person's face)"
        idx += 1
    else:
        kit_str = f"the current official {target_team} suit and cap. MUST include its real sponsor logos exactly as they appear in real life (no invented brands)"

    prompt_lines.append(
        f"INSET: AI-generated portrait of the EXACT SAME person wearing the {target_team} race suit/kit and cap. Crop it tight on his face and place it inside a large circle (about 45% of the card width) with a clean solid white ring (thin but strong) and a soft drop shadow, upper-left. The hero overlaps the right edge of the circle (hero in front) with a soft contact shadow on the circle — real depth, like layered Photoshop layers. The face in the circle must use {alt_str}. The clothing in the circle MUST follow {kit_str}, and MUST be in {target_team} colours ({target_team_color}).\n"
    )

    prompt_lines.append(f"{treatment_clause(treatment, accent)}\n")
    prompt_lines.append(
        f"GRADE & LIGHT: one consistent, cinematic but natural sports-media colour grade across hero, inset and background; high micro-contrast on the face, rich true team colours, soft cool rim light on the hero's edge, clean separation from the background. Looks like a real photograph composited by a professional, not AI art.\n"
    )
    prompt_lines.append(
        f"Bottom 35% MUST be a smooth dark gradient with no faces, no bodies, no objects, completely empty (a headline is added later). Do NOT add any text, letters, numbers, quote marks, badges, page logos, watermarks, glow, neon, lens flares or light streaks."
    )

    return ("\n".join(prompt_lines), refs)


def build_team_radio_prompt(name: str, number: str, colour: str, logo_desc: str, team: str, person_desc: str, lines: list[tuple[str, str]], context: str, weather: str, second_car_desc: str | None = None) -> str:
    radio_lines = "\n".join(f'  ({"team colour, right" if who == "driver" else "white, left"}) "{t}"' for who, t in lines)
    weather_clause = " (no rain or water effects on his face)"
    if weather == "rain":
        weather_clause = ""
        
    dry_clause = ""
    if weather != "rain":
        dry_clause = " Dry conditions: no rain, no wet track, no water spray or wet effects anywhere on the card."
        
    second_car_clause = ""
    if second_car_desc:
        second_car_clause = f" Image 5 is {second_car_desc} racing alongside; keep its real livery, number and sponsor logos exactly."

    return (
        f'Create a premium vertical (4:5) Formula 1 "Team Radio" social media graphic.\n\n'
        f'LAYOUT & STYLE: copy the design of image 3 as closely as possible — a semi-transparent dark glass radio panel on the left, slightly tilted, the driver\'s large face on the center-right, the car racing across the bottom, deep dark background with a glow on the track at the bottom, cinematic professional sports-media finish. Use {colour} as the accent/glow colour. Do not copy the page logo in the top-right corner of image 3.\n\n'
        f'RADIO PANEL: inside the glass panel, follow the card format of image 4 exactly (it shows six example drivers; use the same layout for {name}): top-right the driver surname "{name}" in {colour}, below it "RADIO" in bold white; the big driver number "{number}" in {colour} on the left sitting on the stepped band in the team colour; {logo_desc} at the right under "RADIO"; then the radio conversation in the same wide uppercase F1-style font — driver lines in {colour} aligned right, engineer lines in white aligned left — exactly these lines in this order and nothing else:\n'
        f'{radio_lines}\n'
        f'Spell every word exactly as written, including punctuation. No other text anywhere on the card.\n\n'
        f'PEOPLE & CAR: image 1 is {person_desc} — he is the large face on the center-right; keep his real face, natural skin, expression, hair and every logo on his clothing exactly as in image 1{weather_clause}. Image 2 is his real {team} car — place it racing across the bottom; keep its real livery, number and sponsor logos exactly.{second_car_clause} Mood and lighting should fit this moment: {context}. Never invent or alter a face, car or logo. Photorealistic.{dry_clause}'
    )

def _image_to_b64(path: str, max_side: int = 1536) -> str:
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=92)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _is_quota_error(msg: str, code: int) -> bool:
    if code == 429:
        return True
    msg_low = msg.lower()
    return any(w in msg_low for w in ['quota', 'limit', 'exhausted', 'entitled'])


def generate(db: Session, req: GenerationRequest, *, timeout: float = 600, fanpage_id: int | None = None) -> bytes:
    cfg = get_nine_router_config(db)
    
    settings = db.query(Settings).first()
    daily_max = settings.visual_engine_daily_max if settings else 60

    now_wib = datetime.now(ZoneInfo("Asia/Jakarta"))
    start_of_day = datetime(now_wib.year, now_wib.month, now_wib.day, tzinfo=ZoneInfo("Asia/Jakarta")).astimezone(timezone.utc)
    
    usage_count = db.query(AICopyEvent).filter(
        AICopyEvent.context == 'image_gen',
        AICopyEvent.created_at >= start_of_day
    ).count()
    
    if usage_count >= daily_max:
        raise VisualEngineError('quota', 'daily cap reached')

    images = [_image_to_b64(ref) for ref in req.refs]
    
    body = {
        "model": req.model,
        "prompt": req.prompt,
        "images": images,
        "size": req.size,
        "quality": req.quality
    }

    url = f"{cfg.base_url}/images/generations"
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json"
    }
    
    start_time = datetime.now(timezone.utc)
    
    def record_usage(outcome: str, err: str = ""):
        lat = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
        err_sanitized = err
        if err_sanitized:
            if cfg.api_key:
                err_sanitized = err_sanitized.replace(cfg.api_key, "***")
            import re
            err_sanitized = re.sub(r'([?&](?:key|token|api-key)=)[^&\s"\']+', r'\1***', err_sanitized, flags=re.IGNORECASE)
            
        ev = AICopyEvent(
            context='image_gen',
            outcome=outcome,
            models_tried=req.model,
            final_provider='9router',
            error_message=err_sanitized[:300] if err_sanitized else None,
            latency_ms=lat,
            fanpage_id=fanpage_id
        )
        db.add(ev)
        db.commit()

    for attempt in range(2):
        def sanitize_err(err_text: str) -> str:
            res_err = err_text
            if cfg.api_key:
                res_err = res_err.replace(cfg.api_key, "***")
            import re
            return re.sub(r'([?&](?:key|token|api-key)=)[^&\s"\']+', r'\1***', res_err, flags=re.IGNORECASE)
            
        try:
            with httpx.stream("POST", url, json=body, headers=headers, timeout=timeout) as resp:
                resp.read()
                
                if resp.status_code == 401 or resp.status_code == 403:
                    safe_msg = sanitize_err(resp.text)
                    record_usage('failed', f"Auth error: {safe_msg}")
                    raise VisualEngineError('auth', f"Auth error: {safe_msg}")
                
                if not resp.is_success:
                    if resp.status_code == 429 or _is_quota_error(resp.text, resp.status_code):
                        safe_msg = sanitize_err(resp.text)
                        record_usage('failed', f"Quota error: {safe_msg}")
                        raise VisualEngineError('quota', f"Quota error: {safe_msg}")
                        
                    if attempt == 1:
                        safe_msg = sanitize_err(resp.text)
                        record_usage('failed', f"HTTP {resp.status_code}: {safe_msg}")
                        raise VisualEngineError('transport', f"HTTP {resp.status_code}: {safe_msg}")
                    continue
                    
                data = resp.json()
                if 'data' not in data or not data['data'] or 'b64_json' not in data['data'][0]:
                    record_usage('failed', 'missing b64 in response')
                    raise VisualEngineError('bad_response', 'missing b64 in response')
                
                b64 = data['data'][0]['b64_json']
                record_usage('success')
                return base64.b64decode(b64)
                
        except httpx.TimeoutException as e:
            if attempt == 1:
                record_usage('failed', f"Timeout: {e}")
                raise VisualEngineError('timeout', f"Timeout: {e}")
        except httpx.RequestError as e:
            if attempt == 1:
                record_usage('failed', f"Transport error: {e}")
                raise VisualEngineError('transport', f"Transport error: {e}")
        except VisualEngineError:
            raise
        except Exception as e:
            if attempt == 1:
                record_usage('failed', f"Unexpected error: {str(e)}")
                raise VisualEngineError('transport', f"Unexpected error: {str(e)}")

    raise VisualEngineError('transport', 'Max retries exceeded')
