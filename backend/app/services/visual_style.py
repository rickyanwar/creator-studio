import colorsys
from typing import Any
from sqlalchemy.orm import Session
from app.services.f1_drivers import get_driver

def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"

def _relative_luminance(r: int, g: int, b: int) -> float:
    def process_channel(c: int) -> float:
        c_norm = c / 255.0
        return c_norm / 12.92 if c_norm <= 0.03928 else ((c_norm + 0.055) / 1.055) ** 2.4
    return 0.2126 * process_channel(r) + 0.7152 * process_channel(g) + 0.0722 * process_channel(b)

def _contrast_ratio(l1: float, l2: float) -> float:
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)

def _ensure_text_contrast(hex_str: str, bg_hex: str = "#111111", min_contrast: float = 3.0) -> str:
    r, g, b = _hex_to_rgb(hex_str)
    bg_lum = _relative_luminance(*_hex_to_rgb(bg_hex))
    
    if _contrast_ratio(_relative_luminance(r, g, b), bg_lum) >= min_contrast:
        return hex_str.upper()
        
    h, l, s = colorsys.rgb_to_hls(r/255.0, g/255.0, b/255.0)
    
    while l < 1.0:
        l += 0.01
        if l > 1.0: l = 1.0
        nr, ng, nb = colorsys.hls_to_rgb(h, l, s)
        nr, ng, nb = int(round(nr * 255)), int(round(ng * 255)), int(round(nb * 255))
        if _contrast_ratio(_relative_luminance(nr, ng, nb), bg_lum) >= min_contrast:
            return _rgb_to_hex(nr, ng, nb)
            
    return "#FFFFFF"

def get_color_name(hex_str: str) -> str:
    color_map = {
        "red": (255, 0, 0),
        "orange": (255, 128, 0),
        "yellow": (255, 255, 0),
        "green": (0, 128, 0),
        "teal": (0, 128, 128),
        "blue": (0, 0, 255),
        "navy": (0, 0, 128),
        "purple": (128, 0, 128),
        "pink": (255, 105, 180),
        "silver": (192, 192, 192),
        "white": (255, 255, 255)
    }
    
    try:
        r, g, b = _hex_to_rgb(hex_str)
    except Exception:
        return "silver"
        
    best_dist = float('inf')
    best_name = "silver"
    
    for name, (cr, cg, cb) in color_map.items():
        dist = (r - cr)**2 + (g - cg)**2 + (b - cb)**2
        if dist < best_dist:
            best_dist = dist
            best_name = name
            
    return best_name

def choose_accent(subject_name: str, sport_is_f1: bool, db_or_driver_lookup: Any, fanpage: Any) -> tuple[str, str, str]:
    raw_hex = None
    team_prefix = ""
    
    if sport_is_f1 and db_or_driver_lookup and subject_name:
        try:
            if callable(db_or_driver_lookup):
                driver = db_or_driver_lookup(subject_name)
            elif isinstance(db_or_driver_lookup, Session):
                driver = get_driver(db_or_driver_lookup, subject_name)
            else:
                driver = None
                
            if driver and hasattr(driver, 'team_colour'):
                raw_hex = driver.team_colour
                if not isinstance(raw_hex, str):
                    raw_hex = None
                if hasattr(driver, 'team_name') and driver.team_name and isinstance(driver.team_name, str):
                    team_prefix = f"{driver.team_name} "
        except Exception:
            pass
            
    if not raw_hex and fanpage and hasattr(fanpage, 'design_accent_color') and fanpage.design_accent_color:
        if isinstance(fanpage.design_accent_color, str):
            raw_hex = fanpage.design_accent_color
        else:
            raw_hex = None
            
    if not raw_hex:
        raw_hex = "#C9CED6"
        
    if not raw_hex.startswith('#'):
        raw_hex = f"#{raw_hex}"
        
    raw_hex = raw_hex.upper()
    color_name = get_color_name(raw_hex)
    formatted_name = f"{team_prefix}{color_name} ({raw_hex})"
    
    return raw_hex, _ensure_text_contrast(raw_hex), formatted_name

def choose_treatment(content_type: str, mood: str, weather: str) -> str:
    if weather and weather.lower() == 'rain':
        return "rain"
    mood_lower = (mood or '').lower()
    if mood_lower == 'negative':
        return "dramatic"
    if mood_lower == 'positive':
        return "celebration"
    return "clean"

def treatment_clause(treatment: str, accent_formatted: str) -> str:
    base = (
        "BACKGROUND: the real event backdrop from the photos, blurred into smooth creamy bokeh "
        "(never an invented background), fine film-grain texture, soft vignette. "
    )
    
    if treatment == "rain":
        desc = "Cool steel-blue grade, rain atmosphere only (no heavy fake rain over the face), wet reflections."
    elif treatment == "dramatic":
        desc = f"Low-key, cool desaturated backdrop, {accent_formatted} only as thin rim light. Keep faces well lit and sharp (hard side light, not murky)."
    elif treatment == "celebration":
        desc = f"Brighter warmer backdrop, soft {accent_formatted} light bloom behind subject (no confetti or fireworks graphics)."
    else: # clean
        desc = f"Dark graded backdrop, subtle {accent_formatted} tint + {accent_formatted} rim light."
        
    anti_prompt = "Tasteful and restrained, like a senior sports-media designer, never cheap or busy. No stripes, no lines, no paint/brush/splatter textures, no geometric shapes, no light streaks; depth from light only."
    
    return f"{base}{desc} {anti_prompt}"
