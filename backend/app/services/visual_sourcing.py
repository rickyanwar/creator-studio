import logging
import json
import re
import os
from datetime import date
from sqlalchemy import or_

def is_in_event_window(db, subject_or_niche: str, today: date) -> bool:
    from app.models.gallery import GalleryKeyword
    from app.tasks.gallery_downloader import _in_event_window
    kw = db.query(GalleryKeyword).filter(
        GalleryKeyword.is_active == True,
        or_(
            GalleryKeyword.keyword.ilike(subject_or_niche),
            GalleryKeyword.niche.ilike(subject_or_niche)
        )
    ).first()
    if not kw:
        return False
    return _in_event_window(kw, today)

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, cast, String
from app.models.gallery import GalleryImage
from app.services.ig_content_classifier import PostAnalysis
from app.services.design_images import (
    fetch_subject_datauri,
    _vision_datauri,
    _vision_chat
)

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class PhotoPick:
    path: str
    source: Literal['gallery', 'jina', 'source_post']
    gallery_id: int | None
    url: str | None
    label: str
    reason: str

@dataclass(frozen=True)
class InsetPlan:
    pick: PhotoPick | None
    mode: Literal['real', 'asset', 'imagined']
    context_desc: str

@dataclass(frozen=True)
class PhotoPlan:
    hero: PhotoPick | None
    inset: PhotoPick | None
    insets: tuple[InsetPlan, ...]
    extra: tuple[PhotoPick, ...]
    notes: tuple[str, ...]
    inset_is_distinct: bool = True

def wanted_labels(analysis: PostAnalysis, layout: str) -> dict[str, str]:
    labels = {}
    
    if layout == 'action':
        labels['hero'] = 'action'
    else:
        labels['hero'] = 'face'
        
    if layout == 'inset':
        if analysis.secondary_kind == 'person':
            summary_lower = analysis.moment_summary.lower()
            racing_keywords = ['crash', 'overtake', 'incident', 'lap', 'track', 'race', 'qualifying', 'practice', 'session', 'sector', 'straight']
            if any(k in summary_lower for k in racing_keywords):
                labels['inset'] = 'action'
            else:
                labels['inset'] = 'face'
        else:
            labels['inset'] = 'action'
            
    if analysis.type == 'team_radio':
        labels['extra'] = 'action'
        
    return labels

def build_pick_prompt(num_candidates: int, moment_summary: str, secondary: str, layout: str, role: str) -> str:
    return (
        f"You are picking a photo for the '{role}' slot in a '{layout}' graphic.\n"
        f"The post is about: {moment_summary}\n"
        f"Secondary context: {secondary}\n\n"
        f"We have {num_candidates} candidate photos.\n"
        "Reply ONLY with the 1-based integer index of the candidate that best fits "
        "the mood, subject, and action of the summary. If unsure, reply '1'."
    )

def build_inset_pick_prompt(num_candidates: int, moment_summary: str, inset_context: str, hero_name: str) -> str:
    return (
        f"You are picking a photo for the 'inset' slot in an 'inset' graphic.\n"
        f"The post is about: {moment_summary}\n"
        f"Inset context required: {inset_context}\n"
        f"Hero person to AVOID (including their close-up helmet): {hero_name}\n\n"
        f"We have {num_candidates} candidate photos.\n"
        "Reply ONLY with a JSON object on one line: "
        '{\"index\": <1-based integer>, \"shows_hero_person\": <true|false>}\n'
        "shows_hero_person=true if the candidate clearly shows the hero person's face or their close-up helmet. "
        "If unsure about index, use 1."
    )

def parse_inset_pick(raw: str, n: int) -> tuple[int, bool]:
    """Returns (0-based index, shows_hero_person). Fallback: (0, True) = reject candidate."""
    try:
        match = re.search(r'\{.*?\}', raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            idx = int(parsed.get('index', 1)) - 1
            idx = idx if 0 <= idx < n else 0
            shows_hero = bool(parsed.get('shows_hero_person', True))
            return idx, shows_hero
    except Exception:
        pass
    return 0, True  # conservative: treat as showing hero


def build_whatif_alt_pick_prompt(num_candidates: int, hero_name: str) -> str:
    return (
        f"You are picking an ALTERNATE photo of '{hero_name}' for a what-if graphic.\n"
        "The first image provided is the HERO REFERENCE.\n"
        f"After that, there are {num_candidates} candidate photos.\n"
        "Reply ONLY with a JSON object on one line: "
        '{"index": <1-based integer for the candidate>, "same_person": <true|false>, "different_pose": <true|false>}\n'
        "same_person=true if the candidate is the same person as the hero reference.\n"
        "different_pose=true if the candidate has a clearly different pose, angle, or expression from the hero reference."
    )

def parse_whatif_alt_pick(raw: str, n: int) -> tuple[int, bool, bool]:
    try:
        import json, re
        match = re.search(r'\{.*?\}', raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            idx = int(parsed.get('index', 1)) - 1
            idx = idx if 0 <= idx < n else 0
            same = bool(parsed.get('same_person', False))
            diff = bool(parsed.get('different_pose', False))
            return idx, same, diff
    except Exception:
        pass
    return 0, False, False

def parse_index(raw: str, n: int) -> int:
    match = re.search(r'\d+', raw)
    if match:
        idx = int(match.group())
        idx -= 1
        if 0 <= idx < n:
            return idx
    return 0

def parse_bbox(raw: str) -> dict:
    try:
        match = re.search(r'\{.*?\}', raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            return clamp_bbox({
                "x0": float(parsed.get("x0", 0.0)),
                "y0": float(parsed.get("y0", 0.0)),
                "x1": float(parsed.get("x1", 1.0)),
                "y1": float(parsed.get("y1", 1.0)),
            })
    except Exception:
        pass
    return {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0}

def clamp_bbox(bbox: dict) -> dict:
    clamped = {}
    for k in ['x0', 'y0', 'x1', 'y1']:
        v = bbox.get(k, 0.0)
        v = max(0.0, min(1.0, v))
        clamped[k] = v
    if clamped['x0'] >= clamped['x1']:
        clamped['x0'] = 0.0
        clamped['x1'] = 1.0
    if clamped['y0'] >= clamped['y1']:
        clamped['y0'] = 0.0
        clamped['y1'] = 1.0
    return clamped

def get_name_variants(subject: str) -> list[str]:
    variants = [subject]
    aliases = {'ollie': 'oliver', 'oliver': 'ollie', 'alex': 'alexander', 'alexander': 'alex'}
    parts = subject.strip().lower().split()
    if parts:
        first = parts[0]
        if first in aliases:
            variants.append(aliases[first] + " " + " ".join(parts[1:]).strip())
    # Deduplicate while preserving order
    seen = set()
    return [x for x in variants if not (x.lower() in seen or seen.add(x.lower()))]

def _names_are_same_person(a: str, b: str) -> bool:
    """True if a and b refer to the same person (case-insensitive full name or surname match)."""
    a, b = a.strip().lower(), b.strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    # surname-only: last token of each
    a_surname = a.split()[-1] if a.split() else a
    b_surname = b.split()[-1] if b.split() else b
    if len(a_surname) >= 3 and a_surname == b_surname:
        return True
    return False

def decision_photo_keys(db, story_id: int, exclude_fanpage_id: int) -> set[str]:
    from app.models.radar import RadarStoryDecision
    decisions = db.query(RadarStoryDecision).filter(
        RadarStoryDecision.story_id == story_id,
        RadarStoryDecision.fanpage_id != exclude_fanpage_id,
        RadarStoryDecision.used_photo_key.is_not(None)
    ).all()
    keys = set()
    for d in decisions:
        if d.used_photo_key:
            for k in d.used_photo_key.split('|'):
                if k.strip():
                    keys.add(k.strip())
    return keys


def is_same_person(a: str, b: str) -> bool:
    if not a or not b:
        return False
    a_lower = a.lower()
    b_lower = b.lower()
    if a_lower == b_lower:
        return True
    a_surname = a_lower.split()[-1] if a_lower.split() else a_lower
    b_surname = b_lower.split()[-1] if b_lower.split() else b_lower
    if len(a_surname) >= 3 and a_surname == b_surname:
        return True
    return False

def _find_inset_pick(

    *,
    db,
    context_item: dict,

    hero_subj: str,
    label: str,
    analysis: PostAnalysis,
    layout: str,
    niche: str,
    exclude_keys: set[str],
    notes: list,
) -> 'InsetPlan | None':
    """
    Pick an inset photo based on inset_context.
    Candidates must NOT show the hero person's face or helmet.
    Uses the same vision call to also return shows_hero_person.
    Returns None if no distinct (non-hero) candidate found.
    """
    if not context_item:
        return None
    inset_context = context_item["description"]
    inset_query = context_item["query"]
    if not inset_context:
        return None
        
    if analysis.secondary_kind == 'person' and is_same_person(hero_subj, analysis.secondary):
        notes.append("Inset: secondary person is the hero, rejected")
        return None


    exclude_ids = [int(k) for k in exclude_keys if str(k).lstrip('-').isdigit()]
    exclude_paths_or_urls = [k for k in exclude_keys if not str(k).lstrip('-').isdigit()]

    # --- Gallery candidates ---
    base_query = db.query(GalleryImage).filter(
        GalleryImage.is_deleted == False,
        GalleryImage.label == label
    )
    if exclude_ids:
        base_query = base_query.filter(GalleryImage.id.notin_(exclude_ids))
    if exclude_paths_or_urls:
        base_query = base_query.filter(
            GalleryImage.local_path.notin_(exclude_paths_or_urls),
            or_(
                GalleryImage.source_image_url.is_(None),
                GalleryImage.source_image_url.notin_(exclude_paths_or_urls),
            ),
        )

    # Search by inset_context keywords
    context_terms = [t.strip().lower() for t in inset_context.replace(',', ' ').split() if len(t.strip()) >= 3]
    if context_terms:
        from sqlalchemy import or_ as sql_or, cast, String
        ilike_conds = []
        for t in context_terms[:4]:
            ilike_conds.append(GalleryImage.keyword.ilike(f"%{t}%"))
            ilike_conds.append(cast(GalleryImage.extra_keywords, String).ilike(f"%{t}%"))
        candidates = base_query.filter(sql_or(*ilike_conds)).order_by(
            GalleryImage.captured_at.desc().nulls_last(),
            GalleryImage.downloaded_at.desc()
        ).limit(6).all()
    else:
        candidates = []

    if candidates:
        content = [{
            "type": "text",
            "text": build_inset_pick_prompt(
                len(candidates), analysis.moment_summary, inset_context, hero_subj
            )
        }]
        for c in candidates:
            try:
                with open(c.local_path, "rb") as f:
                    uri = _vision_datauri(f.read())
                content.append({"type": "image_url", "image_url": {"url": uri}})
            except Exception:
                pass
        try:
            raw = _vision_chat(content, context="vision_pick")
            idx, shows_hero = parse_inset_pick(raw, len(candidates))
        except Exception:
            idx, shows_hero = 0, True
            notes.append("Vision pick failed for inset, treating as shows_hero")

        if not shows_hero:
            winner = candidates[idx]
            notes.append(f"Inset: picked gallery index {idx+1}, shows_hero_person=False")
            return InsetPlan(pick=PhotoPick(
                path=winner.local_path,
                source="gallery",
                gallery_id=winner.id,
                url=winner.source_image_url,
                label=label,
                reason=f"Inset: vision picked index {idx+1}, hero absent"
            ), mode='real', context_desc=inset_context)
        else:
            notes.append(f"Inset: gallery candidate shows hero person, rejected")

    # --- Jina fallback (Gated by Event Window) ---
    from datetime import datetime
    import pytz
    # Use timezone known to viral-radar backend (Asia/Jakarta is _WIB)
    wib_tz = pytz.timezone("Asia/Jakarta")
    today = datetime.now(wib_tz).date()
    
    # Try hero first, then niche
    in_event = is_in_event_window(db, hero_subj, today)
    if not in_event:
        in_event = is_in_event_window(db, niche, today)

    if inset_query and in_event:
        try:
            jina_query = f"{inset_query} {today.strftime('%Y-%m-%d')} today"
            datauri, gi = fetch_subject_datauri(
                db, jina_query, image_type=label, niche=niche,
                exclude_paths=set(exclude_paths_or_urls)
            )
            if gi:
                try:
                    with open(gi.local_path, "rb") as f:
                        uri = _vision_datauri(f.read())
                    check_content = [
                        {"type": "text", "text": (
                            f"Does this photo show '{hero_subj}' or their close-up helmet? "
                            "Reply ONLY with JSON: {\"shows_hero_person\": true} or {\"shows_hero_person\": false}"
                        )},
                        {"type": "image_url", "image_url": {"url": uri}}
                    ]
                    raw_check = _vision_chat(check_content, context="vision_pick")
                    _, shows_hero = parse_inset_pick(raw_check, 1)
                except Exception:
                    shows_hero = True

                if not shows_hero:
                    notes.append("Inset: Jina fallback used (in event window), hero absent")
                    return InsetPlan(pick=PhotoPick(
                        path=gi.local_path,
                        source="jina",
                        gallery_id=gi.id,
                        url=gi.source_image_url,
                        label=label,
                        reason="Inset: Jina fetch (in event window), hero absent"
                    ), mode='real', context_desc=inset_context)
                else:
                    notes.append("Inset: Jina result shows hero person, rejected")
        except Exception as e:
            notes.append(f"Inset Jina fallback failed: {e}")
    elif inset_query and not in_event:
        notes.append("Inset: Jina fallback skipped (not in event window)")

    # (b) Asset
    asset_pick = None
    if 'f1' in niche.lower():
        from app.models.f1_drivers import F1Driver
        from sqlalchemy import or_ as sql_or
        # Try to find a team logo
        context_lower = inset_context.lower()
        drivers = db.query(F1Driver).all()
        for d in drivers:
            if d.team_name.lower() in context_lower or (d.surname and d.surname.lower() in context_lower):
                if d.team_logo_path:
                    asset_pick = PhotoPick(path=d.team_logo_path, source="gallery", gallery_id=None, url=None, label="asset", reason=f"Asset: matched team {d.team_name}")
                    break
    if asset_pick:
        notes.append(f"Inset: using asset mode with team logo {asset_pick.path}")
        return InsetPlan(pick=asset_pick, mode='asset', context_desc=inset_context)

    # (c) Imagined
    notes.append("Inset: no real or asset found, using imagined mode")
    return InsetPlan(pick=None, mode='imagined', context_desc=inset_context)



def plan_photos(db, analysis: PostAnalysis, layout: str, niche: str, source_image_path: str | None, exclude_keys: set[str]) -> PhotoPlan:
    notes = []
    w_labels = wanted_labels(analysis, layout)
    created_temp_paths = []
    
    try:
        def _find_pick(subject: str, label: str, role: str) -> PhotoPick | None:
            if not subject:
                return None
                
            is_inset_event = (role == 'inset' and analysis.secondary_kind == 'event')
            prioritize_crop = (analysis.unique_moment and is_inset_event)
            
            exclude_ids = [int(k) for k in exclude_keys if str(k).lstrip('-').isdigit()]
            exclude_paths_or_urls = [k for k in exclude_keys if not str(k).lstrip('-').isdigit()]
            
            def do_crop(reason_text: str) -> PhotoPick | None:
                if not source_image_path:
                    return None
                import tempfile
                from PIL import Image
                try:
                    prompt = f"Return a JSON bounding box {{\"x0\":0.0, \"y0\":0.0, \"x1\":1.0, \"y1\":1.0}} for '{subject}' AND the context (e.g. onboard view / the car on track)."
                    with open(source_image_path, "rb") as f:
                        uri = _vision_datauri(f.read())
                    content = [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": uri}}
                    ]
                    raw_bbox = _vision_chat(content, context="crop")
                    bbox = parse_bbox(raw_bbox)
                    
                    area = (bbox['x1'] - bbox['x0']) * (bbox['y1'] - bbox['y0'])
                    if area < 0.25:
                        notes.append(f"Crop fallback failed for {role}: crop area too small ({area:.2f})")
                        return None
                    
                    img = Image.open(source_image_path)
                    w, h = img.size
                    crop_box = (
                        int(bbox['x0'] * w),
                        int(bbox['y0'] * h),
                        int(bbox['x1'] * w),
                        int(bbox['y1'] * h)
                    )
                    cropped = img.crop(crop_box)
                    fd, tmp_path = tempfile.mkstemp(suffix=".jpg", prefix="crop_")
                    os.close(fd)
                    created_temp_paths.append(tmp_path)
                    cropped.convert("RGB").save(tmp_path, "JPEG")
                    notes.append(f"Cropped {subject} from source_post")
                    return PhotoPick(
                        path=tmp_path,
                        source="source_post",
                        gallery_id=None,
                        url=None,
                        label=label,
                        reason=reason_text
                    )
                except Exception as e:
                    notes.append(f"Crop fallback failed for {role}: {e}")
                    return None

            def do_gallery() -> PhotoPick | None:
                variants = get_name_variants(subject)
                width_condition = GalleryImage.width < GalleryImage.height if label == 'face' else GalleryImage.width > GalleryImage.height
                
                base_query = db.query(GalleryImage).filter(
                    GalleryImage.is_deleted == False,
                    GalleryImage.label == label,
                    width_condition
                )
                if exclude_ids:
                    base_query = base_query.filter(GalleryImage.id.notin_(exclude_ids))
                if exclude_paths_or_urls:
                    # NULL-safe: a bare NOT(a IN x OR b IN x) is NULL when
                    # source_image_url is NULL, which would drop the row.
                    base_query = base_query.filter(
                        GalleryImage.local_path.notin_(exclude_paths_or_urls),
                        or_(
                            GalleryImage.source_image_url.is_(None),
                            GalleryImage.source_image_url.notin_(exclude_paths_or_urls),
                        ),
                    )
                    
                # 1. Exact match attempt
                exact_conds = []
                for v in variants:
                    exact_conds.append(func.lower(GalleryImage.keyword) == v.lower())
                    exact_conds.append(GalleryImage.extra_keywords.any(v))
                    
                query_exact = base_query.filter(or_(*exact_conds))
                candidates = query_exact.order_by(
                    GalleryImage.captured_at.desc().nulls_last(),
                    GalleryImage.downloaded_at.desc()
                ).limit(6).all()
                
                # 2. Surname ILIKE match attempt if exact fails
                if not candidates:
                    surnames = [v.strip().split()[-1].lower() for v in variants if v.strip() and len(v.strip().split()[-1]) >= 3]
                    if surnames:
                        ilike_conds = []
                        for s in set(surnames):
                            ilike_conds.append(GalleryImage.keyword.ilike(f"%{s}%"))
                            ilike_conds.append(cast(GalleryImage.extra_keywords, String).ilike(f"%{s}%"))
                        
                        if ilike_conds:
                            query_ilike = base_query.filter(or_(*ilike_conds))
                            candidates = query_ilike.order_by(
                                GalleryImage.captured_at.desc().nulls_last(),
                                GalleryImage.downloaded_at.desc()
                            ).limit(6).all()

                if candidates:
                    content = [{"type": "text", "text": build_pick_prompt(len(candidates), analysis.moment_summary, analysis.secondary, layout, role)}]
                    for c in candidates:
                        try:
                            with open(c.local_path, "rb") as f:
                                uri = _vision_datauri(f.read())
                            content.append({"type": "image_url", "image_url": {"url": uri}})
                        except Exception:
                            pass
                    try:
                        raw_idx = _vision_chat(content, context="vision_pick")
                        idx = parse_index(raw_idx, len(candidates))
                    except Exception:
                        idx = 0
                        notes.append(f"Vision pick failed for {role}, using 0")
                    
                    winner = candidates[idx]
                    return PhotoPick(
                        path=winner.local_path,
                        source="gallery",
                        gallery_id=winner.id,
                        url=winner.source_image_url,
                        label=label,
                        reason=f"Vision picked index {idx+1} from {len(candidates)} gallery candidates"
                    )
                return None

            def do_jina() -> PhotoPick | None:
                try:
                    datauri, gi = fetch_subject_datauri(db, subject, image_type=label, niche=niche, exclude_paths=set(exclude_paths_or_urls))
                    if gi:
                        notes.append(f"Jina fallback used for {role}")
                        return PhotoPick(
                            path=gi.local_path,
                            source="jina",
                            gallery_id=gi.id,
                            url=gi.source_image_url,
                            label=label,
                            reason="Fetched from Jina fallback"
                        )
                except Exception as e:
                    notes.append(f"Jina fallback failed for {role}: {e}")
                return None

            # Execute fallback chain
            if prioritize_crop:
                pick = do_crop("unique_moment fallback")
                if pick:
                    return pick
                    
            pick = do_gallery()
            if pick:
                return pick
                
            pick = do_jina()
            if pick:
                return pick
                
            if not prioritize_crop:
                pick = do_crop("still nothing fallback")
                if pick:
                    return pick
                    
            notes.append(f"Nothing found for {role}")
            return None


        hero_subj = analysis.speaker or analysis.main_subject
        hero = _find_pick(hero_subj, w_labels.get('hero', 'face'), 'hero')
        
        insets_list = []
        if layout in ('inset', 'inset2'):
            for ctx in getattr(analysis, 'inset_contexts', ()):
                plan_item = _find_inset_pick(
                    db=db,
                    context_item=ctx,
                    hero_subj=hero_subj,
                    label=w_labels.get('inset', 'action'),
                    analysis=analysis,
                    layout=layout,
                    niche=niche,
                    exclude_keys=exclude_keys,
                    notes=notes,
                )
                if plan_item:
                    insets_list.append(plan_item)
                    
        inset_is_distinct = len(insets_list) > 0
        inset = insets_list[0].pick if insets_list and insets_list[0].pick else None

            
        extra = []
        if analysis.type == 'team_radio' and w_labels.get('extra'):
            # 1. Hero's car
            hero_car_pick = _find_pick(hero_subj, w_labels['extra'], 'extra')
            if hero_car_pick:
                extra.append(hero_car_pick)
                
            # 2. Up to 1 other involved driver
            for p in analysis.people:
                if p.lower() != hero_subj.lower():
                    other_car_pick = _find_pick(p, w_labels['extra'], 'extra')
                    if other_car_pick:
                        extra.append(other_car_pick)
                    break
                    

        alt_pick = None
        kit_pick = None

        alt_pick = None
        kit_pick = None
        scenario = getattr(analysis, 'scenario', 'none')
        target = getattr(analysis, 'target_team', '').strip()
        if scenario in ('transfer', 'rumor', 'hypothetical') and target:

            if hero and hero.source == 'gallery':
                # Find alt pick
                variants = get_name_variants(hero_subj)
                exclude_ids = [int(k) for k in exclude_keys if str(k).lstrip('-').isdigit()]
                if hero.gallery_id:
                    exclude_ids.append(hero.gallery_id)
                exclude_paths_or_urls = [k for k in exclude_keys if not str(k).lstrip('-').isdigit()]
                
                base_query = db.query(GalleryImage).filter(
                    GalleryImage.is_deleted == False,
                    GalleryImage.label == w_labels.get('hero', 'face')
                )
                if exclude_ids:
                    base_query = base_query.filter(GalleryImage.id.notin_(exclude_ids))
                if exclude_paths_or_urls:
                    base_query = base_query.filter(
                        GalleryImage.local_path.notin_(exclude_paths_or_urls),
                        or_(
                            GalleryImage.source_image_url.is_(None),
                            GalleryImage.source_image_url.notin_(exclude_paths_or_urls),
                        ),
                    )
                
                exact_conds = []
                for v in variants:
                    exact_conds.append(func.lower(GalleryImage.keyword) == v.lower())
                    exact_conds.append(GalleryImage.extra_keywords.any(v))
                
                candidates = base_query.filter(or_(*exact_conds)).order_by(
                    GalleryImage.captured_at.desc().nulls_last(),
                    GalleryImage.downloaded_at.desc()
                ).limit(6).all()
                
                if not candidates:
                    surnames = [v.strip().split()[-1].lower() for v in variants if v.strip() and len(v.strip().split()[-1]) >= 3]
                    if surnames:
                        ilike_conds = []
                        for s in set(surnames):
                            ilike_conds.append(GalleryImage.keyword.ilike(f"%{s}%"))
                            ilike_conds.append(cast(GalleryImage.extra_keywords, String).ilike(f"%{s}%"))
                        if ilike_conds:
                            candidates = base_query.filter(or_(*ilike_conds)).order_by(
                                GalleryImage.captured_at.desc().nulls_last(),
                                GalleryImage.downloaded_at.desc()
                            ).limit(6).all()
                
                if candidates:
                    content = [{"type": "text", "text": build_whatif_alt_pick_prompt(len(candidates), hero_subj)}]
                    try:
                        with open(hero.path, "rb") as f:
                            uri = _vision_datauri(f.read())
                        content.append({"type": "image_url", "image_url": {"url": uri}})
                    except Exception:
                        pass
                        
                    for c in candidates:
                        try:
                            with open(c.local_path, "rb") as f:
                                uri = _vision_datauri(f.read())
                            content.append({"type": "image_url", "image_url": {"url": uri}})
                        except Exception:
                            pass
                            
                    try:
                        raw_idx = _vision_chat(content, context="vision_pick")
                        idx, same_person, diff_pose = parse_whatif_alt_pick(raw_idx, len(candidates))
                    except Exception:
                        idx, same_person, diff_pose = 0, True, True
                        notes.append(f"Vision pick failed for alt, using index 1")
                    
                    if same_person and diff_pose:
                        winner = candidates[idx]
                        notes.append(f"Alt pick: picked gallery index {idx+1}")
                        alt_pick = PhotoPick(
                            path=winner.local_path,
                            source="gallery",
                            gallery_id=winner.id,
                            url=winner.source_image_url,
                            label=w_labels.get('hero', 'face'),
                            reason=f"Alt pick: same person, diff pose"
                        )
                    else:
                        notes.append(f"Alt pick rejected: same_person={same_person}, different_pose={diff_pose}. Using hero as alt, different head angle instructed.")
                else:
                    notes.append("Alt pick: no candidates found. Using hero as alt, different head angle instructed.")

            # Kit pick
            if getattr(analysis, 'target_team', None) and 'f1' in niche.lower():
                from app.models.f1_drivers import F1Driver
                driver = db.query(F1Driver).filter(F1Driver.team_name.ilike(f"%{analysis.target_team}%")).order_by(F1Driver.season.desc()).first()
                if driver:
                    kit_subj = driver.surname if driver.surname else driver.full_name
                    kit_pick = _find_pick(kit_subj, w_labels.get('hero', 'face'), 'extra')
                    if kit_pick:
                        notes.append(f"Kit pick: found {kit_subj} for team {analysis.target_team}")
                    else:
                        notes.append(f"Kit pick: no photo found for {kit_subj} ({analysis.target_team})")
                else:
                    notes.append(f"Kit pick: no F1 driver found for team {analysis.target_team}")
                    
            if alt_pick:
                extra.append(alt_pick)
            if kit_pick:
                extra.append(kit_pick)

        return PhotoPlan(
            hero=hero,
            inset=inset,
            insets=tuple(insets_list),
            extra=tuple(extra),
            notes=tuple(notes),
            inset_is_distinct=inset_is_distinct
        )
    except Exception:
        for tmp in created_temp_paths:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        raise

