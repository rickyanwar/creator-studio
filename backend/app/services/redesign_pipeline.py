import base64
import os
from pathlib import Path
import uuid
import logging
from dataclasses import dataclass
from typing import Literal
import time

from sqlalchemy.orm import Session
from app.models.publish_jobs import PublishJob
from app.models.target_fanpages import TargetFanpage
from app.models.posts import Post
from app.models.radar import RadarStoryDecision

from app.services.ig_content_classifier import analyze_ig_post, is_f1_niche, PostAnalysis
from app.services.f1_drivers import get_driver
from app.services.visual_sourcing import plan_photos, decision_photo_keys
from app.services.layout_chooser import choose_layout
from app.services.visual_engine import (
    GenerationRequest, generate, VisualEngineError,
    build_inset_prompt, build_solo_prompt, build_action_prompt, build_team_radio_prompt, build_whatif_inset_prompt
)
from app.services.visual_qa import check_generated
from app.services.upscaler import upscale_image_bytes

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class RedesignResult:
    ok: bool
    layout: str
    image_path: str | None
    image_url: str | None
    final_text_rendered_by: Literal['ai', 'template']
    notes: list[str]
    problems: list[str]
    content_type: str | None
    mood: str = 'neutral'   # from PostAnalysis.mood; used in run.json / SUMMARY

_VISUAL_REFS_DIR = Path(__file__).resolve().parent.parent / "assets" / "visual_refs"


def redesign_card(
    db: Session,
    *,
    post: Post,
    fanpage: TargetFanpage,
    job: PublishJob | None,
    decision: RadarStoryDecision | None = None,
    pre_analysis: PostAnalysis | None = None,
) -> RedesignResult:
    notes = []
    problems = []
    start_time = time.time()
    plan = None

    source_image_path = post.image_local_paths[0] if post.image_local_paths else None
    if not source_image_path:
        return RedesignResult(ok=False, layout='solo', image_path=None, image_url=None, final_text_rendered_by='template', notes=notes, problems=["No source image"], content_type=None)

    with open(source_image_path, "rb") as f:
        source_image_bytes = f.read()

    niches = (fanpage.radar_niches or []) + (fanpage.mode2_gallery_niches or [])
    niche = (niches[0] if niches else "") or "general"
    is_f1 = is_f1_niche(niches)
    
    try:
        if pre_analysis is not None:
            analysis = pre_analysis
            notes.append(f"Reused pre-analysis: {analysis.type}")
        else:
            analysis = analyze_ig_post(
                source_image_bytes, 
                post.original_caption or "", 
                niche=niche, 
                allow_team_radio=is_f1
            )
            notes.append(f"Classified as {analysis.type}")

        # 2. Plan photos and choose layout
        hero_label = "action" if analysis.type == "news" and analysis.secondary_kind == "none" else "face"
        provisional_layout = choose_layout(
            analysis, 
            is_f1=is_f1, 
            hero_label=hero_label, 
            has_inset_photo=True,
            inset_is_distinct=True,
        ).layout

        exclude_keys = set()
        if decision and decision.story_id:
            exclude_keys = decision_photo_keys(db, decision.story_id, fanpage.id)

        plan = plan_photos(
            db, 
            analysis, 
            layout=provisional_layout, 
            niche=niche, 
            source_image_path=source_image_path, 
            exclude_keys=exclude_keys
        )
        notes.extend(plan.notes)

        valid_inset_count = 1
        if hasattr(plan, 'insets'):
            valid_inset_count = len([i for i in plan.insets if i.mode in ('real', 'asset', 'imagined')])
            
        layout = choose_layout(
            analysis,
            is_f1=is_f1,
            hero_label=hero_label,
            has_inset_photo=(plan.inset is not None),
            inset_is_distinct=plan.inset_is_distinct,
            valid_inset_count=valid_inset_count,
        ).layout
        notes.append(f"Final layout: {layout}")

        if not plan.hero:
            problems.append("No hero photo found")
            return RedesignResult(ok=False, layout=layout, image_path=None, image_url=None, final_text_rendered_by='template', notes=notes, problems=problems, content_type=analysis.type, mood=analysis.mood)

        # 3. Team radio
        driver = None
        if layout == 'team_radio':
            speaker = analysis.speaker or analysis.main_subject
            driver = get_driver(db, speaker)
            if not driver:
                notes.append(f"No driver found for '{speaker}', falling back to quote")
                layout = 'inset' if plan.inset else 'solo'

        sport = "Formula 1" if is_f1 else ("MotoGP" if "motogp" in niche.lower() else ("UFC" if "ufc" in niche.lower() else niche))
        from app.services.visual_style import choose_accent, choose_treatment
        raw_accent, text_safe_accent, accent_formatted = choose_accent(analysis.main_subject, is_f1, db, fanpage)
        treatment = choose_treatment(analysis.type, analysis.mood, analysis.weather)
        
        notes.append(f"Style: {treatment} with {accent_formatted}")

        # 4. Build GenerationRequest
        prompt = ""
        refs = []
        
        if layout == 'team_radio' and driver:
            if not plan.extra or len(plan.extra) < 1:
                problems.append("No car photo found for team radio")
                return RedesignResult(ok=False, layout=layout, image_path=None, image_url=None, final_text_rendered_by='template', notes=notes, problems=problems, content_type=analysis.type, mood=analysis.mood)
                
            # Order must match build_team_radio_prompt's text: image 1 face,
            # image 2 car, image 3 Hadjar style, image 4 radio format, image 5 second car.
            refs.append(plan.hero.path)
            refs.append(plan.extra[0].path)
            refs.append(str(_VISUAL_REFS_DIR / "style_hadjar.png"))
            refs.append(str(_VISUAL_REFS_DIR / "style_radio_format.png"))
            second_car_desc = None
            if len(plan.extra) > 1:
                refs.append(plan.extra[1].path)
                second_car_desc = analysis.secondary
                
            prompt = build_team_radio_prompt(
                name=driver.full_name or driver.surname,
                number=str(driver.number),
                colour=driver.team_colour,
                logo_desc=f"{driver.team_name} logo",
                team=driver.team_name,
                person_desc=analysis.main_subject,
                lines=list(analysis.radio_lines),
                context=analysis.moment_summary,
                weather=analysis.weather,
                second_car_desc=second_car_desc
            )
        elif layout in ('inset', 'inset2'):
            scenario = getattr(analysis, 'scenario', 'none')
            target = getattr(analysis, 'target_team', '').strip()
            
            if scenario in ('transfer', 'rumor', 'hypothetical') and target:
                target_team = getattr(analysis, 'target_team', '').strip()
                notes.append(f"What-if scenario: {analysis.scenario} to {target_team}")
                team_color = "the team's primary color"
                team_logo = target_team
                if is_f1:
                    from app.models.f1_drivers import F1Driver
                    dr = db.query(F1Driver).filter(F1Driver.team_name.ilike(f"%{target_team}%")).first()
                    if dr:
                        team_color = dr.team_colour
                        if dr.team_logo_path:
                            team_logo = f"{target_team} logo"
                            
                alt_path = None
                kit_path = None
                for ex in plan.extra:
                    if "Alt pick:" in ex.reason or "same person" in ex.reason:
                        alt_path = ex.path
                    elif "Kit pick:" in ex.reason or "found" in ex.reason:
                        kit_path = ex.path
                        
                prompt, built_refs = build_whatif_inset_prompt(
                    hero_desc=analysis.main_subject,
                    target_team=target_team,
                    target_team_color=team_color,
                    target_team_logo_desc=team_logo,
                    accent=accent_formatted,
                    hero_path=plan.hero.path,
                    alt_path=alt_path,
                    kit_path=kit_path,
                    sport=sport,
                    treatment=treatment
                )
                refs = built_refs
            else:
                inset_args = []
                for inst in plan.insets:
                    if inst.mode in ('real', 'asset', 'imagined'):
                        inset_args.append({
                            'mode': inst.mode,
                            'desc': inst.context_desc,
                            'path': inst.pick.path if inst.pick else None
                        })
                
                prompt, built_refs = build_inset_prompt(
                    hero_desc=analysis.main_subject,
                    insets=inset_args,
                    purpose=analysis.type,
                    accent=accent_formatted,
                    hero_path=plan.hero.path,
                    sport=sport,
                    treatment=treatment
                )
                refs = built_refs
        elif layout == 'solo':
            refs.append(plan.hero.path)
            prompt = build_solo_prompt(
                    hero_desc=analysis.main_subject,
                    purpose=analysis.type,
                    accent=accent_formatted,
                    sport=sport,
                    treatment=treatment
                )
        elif layout == 'action':
            refs.append(plan.hero.path)
            prompt = build_action_prompt(
                subject_desc=analysis.main_subject,
                purpose=analysis.type,
                accent=accent_formatted,
                sport=sport,
                treatment=treatment
            )
        else:
            refs.append(plan.hero.path)
            prompt = build_solo_prompt(
                hero_desc=analysis.main_subject,
                purpose=analysis.type,
                accent=accent_formatted,
                sport=sport,
                treatment=treatment
            )

        req = GenerationRequest(
            layout=layout,
            refs=tuple(refs),
            prompt=prompt
        )

        try:
            gen_bytes = generate(db, req, timeout=300, fanpage_id=fanpage.id)
        except VisualEngineError as e:
            problems.append(f"VisualEngineError: {str(e)}")
            return RedesignResult(ok=False, layout=layout, image_path=None, image_url=None, final_text_rendered_by='template', notes=notes, problems=problems, content_type=analysis.type, mood=analysis.mood)

        # 5. QA
        expected_lines = ()
        expected_header = ()
        if layout == 'team_radio' and driver:
            expected_lines = tuple(t for who, t in analysis.radio_lines)
            expected_header = (driver.name.upper(), f"#{driver.number}")

        whatif_target_team = getattr(analysis, 'target_team', None) if getattr(analysis, 'scenario', 'none') != 'none' else None
        
        source_branding = (f"@{post.ig_source.ig_username}",) if post.ig_source else ()
        
        inset_ref_paths = []
        inset_modes = []
        if layout in ('inset', 'inset2'):
            if whatif_target_team:
                pass
            else:
                for inst in getattr(plan, 'insets', []):
                    if inst.mode in ('real', 'asset', 'imagined'):
                        if inst.pick and inst.pick.path:
                            inset_ref_paths.append(inst.pick.path)
                        inset_modes.append(inst.mode)
                        
        qa_res = check_generated(
            gen_bytes,
            layout=layout,
            hero_ref_path=refs[0],
            inset_ref_paths=tuple(inset_ref_paths),
            inset_modes=tuple(inset_modes),
            expected_lines=expected_lines,
            expected_header=expected_header,
            whatif_target_team=whatif_target_team,
            source_branding=source_branding
        )

        if not qa_res.passed:
            notes.append("QA failed, retrying once")
            if time.time() - start_time > 1200:
                problems.append("Budget exceeded")
                return RedesignResult(ok=False, layout=layout, image_path=None, image_url=None, final_text_rendered_by='template', notes=notes, problems=problems, content_type=analysis.type, mood=analysis.mood)
            
            try:
                gen_bytes = generate(db, req, timeout=300, fanpage_id=fanpage.id)
                inset_ref_paths = []
                inset_modes = []
                if layout in ('inset', 'inset2'):
                    if whatif_target_team:
                        pass
                    else:
                        for inst in getattr(plan, 'insets', []):
                            if inst.mode in ('real', 'asset', 'imagined'):
                                if inst.pick and inst.pick.path:
                                    inset_ref_paths.append(inst.pick.path)
                                inset_modes.append(inst.mode)
                                
                qa_res = check_generated(
                    gen_bytes,
                    layout=layout,
                    hero_ref_path=refs[0],
                    inset_ref_paths=tuple(inset_ref_paths),
                    inset_modes=tuple(inset_modes),
                    expected_lines=expected_lines,
                    expected_header=expected_header,
                    whatif_target_team=whatif_target_team,
                    source_branding=source_branding
                )
            except VisualEngineError as e:
                problems.append(f"Retry VisualEngineError: {str(e)}")
                return RedesignResult(ok=False, layout=layout, image_path=None, image_url=None, final_text_rendered_by='template', notes=notes, problems=problems, content_type=analysis.type, mood=analysis.mood)

        if not qa_res.passed:
            problems.extend(qa_res.problems)
            return RedesignResult(ok=False, layout=layout, image_path=None, image_url=None, final_text_rendered_by='template', notes=notes, problems=problems, content_type=analysis.type, mood=analysis.mood)

        # 6. Non-radio: render through the fanpage's own template (same as other posts).
        # template_name is NOT hardcoded — use the job's pre-resolved template_id, or
        # call resolve_template for preview (job=None) exactly as recreate_post_for_fanpage does.
        final_text_rendered_by = 'ai' if layout == 'team_radio' else 'template'
        
        if layout != 'team_radio':
            from app.services.design_images import prepare_design_images
            from app.models.design_templates import DesignTemplate
            import httpx
            from app.config import get_settings
            from app.tasks.ig_recreate import _RENDER_TIMEOUT
            from app.services.design_images import resolve_template as _resolve_template

            if job and job.design_template_id:
                tpl = db.query(DesignTemplate).filter_by(id=job.design_template_id).first()
            else:
                # Preview path (job=None): resolve same way recreate_post_for_fanpage does.
                # team_radio→quote, other→news (mapping done before reaching here).
                tpl_ctype = "quote" if analysis.type in ("quote", "team_radio") else (
                    "news" if analysis.type in ("news", "other") else analysis.type
                )
                tpl = _resolve_template(db, tpl_ctype, fanpage=fanpage)

            if not tpl or not tpl.template_json:
                problems.append(
                    f"No template for fanpage '{fanpage.name}' / type '{analysis.type}' "
                    "(set default_quote_template_id or default_news_template_id on the fanpage)"
                )
                return RedesignResult(ok=False, layout=layout, image_path=None, image_url=None, final_text_rendered_by=final_text_rendered_by, notes=notes, problems=problems, content_type=analysis.type, mood=analysis.mood)

            b64 = base64.b64encode(gen_bytes).decode()
            image_src = f"data:image/png;base64,{b64}"
            
            from app.tasks.design_renderer import clean_title_for_render, clear_template_text_objects
            d_title = job.design_title if job else analysis.moment_summary
            d_title = clean_title_for_render(d_title)
            
            if analysis.type in ("quote", "news", "team_radio") and not d_title:
                err = "empty design_title"
                if job:
                    job.last_error = err
                    db.commit()
                if decision:
                    decision.preview_status = "failed"
                    db.commit()
                problems.append(err)
                return RedesignResult(ok=False, layout=layout, image_path=None, image_url=None, final_text_rendered_by=final_text_rendered_by, notes=notes, problems=problems, content_type=analysis.type, mood=analysis.mood)

            d_subtitle = job.design_subtitle if job else ""
            d_caption = job.design_caption if job else ""

            template_json, image_srcs = prepare_design_images(
                db, tpl.template_json, tpl.canvas_width,
                d_title or "", niche, image_src, expand=False
            )
            
            # Zero baked sample text so the renderer fills from title/subtitle/caption.
            # No colour/icon override — fanpage templates render exactly as configured.
            template_json = clear_template_text_objects(template_json)
            
            from app.services.design_images import watermark_datauri, focus_points_for, _safe_face_cy_ceiling
            settings = get_settings()
            try:
                resp = httpx.post(
                    f"{settings.renderer_url.rstrip('/')}/render",
                    json={
                        "template_json": template_json,
                        "width": tpl.canvas_width,
                        "height": tpl.canvas_height,
                        "title": d_title,
                        "subtitle": d_subtitle,
                        "caption": d_caption,
                        "watermark": fanpage.watermark_text or "",
                        "watermark_image": watermark_datauri(fanpage),
                        "image_srcs": image_srcs,
                        "focus_points": focus_points_for(
                            image_srcs, safe_cy_ceiling=_safe_face_cy_ceiling(template_json, tpl.canvas_height)
                        ),
                        "scale": settings.design_render_scale,
                    },
                    timeout=_RENDER_TIMEOUT,
                )
                resp.raise_for_status()
                gen_bytes = resp.content
            except Exception as e:
                problems.append(f"Renderer error: {str(e)}")
                return RedesignResult(ok=False, layout=layout, image_path=None, image_url=None, final_text_rendered_by=final_text_rendered_by, notes=notes, problems=problems, content_type=analysis.type, mood=analysis.mood)

        # 7. Upscale
        gen_bytes = upscale_image_bytes(gen_bytes, target_edge=2700)

        # Save
        import hashlib
        from pathlib import Path
        h = hashlib.md5(gen_bytes).hexdigest()[:8]
        fname = f"redesign_{uuid.uuid4().hex[:8]}_{h}.png"
        from app.config import get_settings
        settings = get_settings()
        
        designs_dir = Path(settings.storage_base_path) / "designs"
        os.makedirs(designs_dir, exist_ok=True)
        save_path = str(designs_dir / fname)
        with open(save_path, "wb") as f:
            f.write(gen_bytes)
            
        url = f"{settings.storage_base_url.rstrip('/')}/designs/{fname}"

        # 8. Record used_photo_key
        if decision:
            keys = []
            # Canonical D15 keys: local path + source URL of every gallery/jina
            # photo (both exclusion readers match on these); source-post crops
            # are temp files and are never reused, so they are not recorded.
            for p in [plan.hero, plan.inset] + list(plan.extra):
                if not p or p.source == 'source_post':
                    continue
                for k in (p.path, p.url):
                    if isinstance(k, str) and k and k not in keys:
                        keys.append(k)
            if keys:
                existing = [k for k in (decision.used_photo_key or '').split('|') if k]
                decision.used_photo_key = '|'.join(dict.fromkeys(existing + keys))

        return RedesignResult(ok=True, layout=layout, image_path=save_path, image_url=url, final_text_rendered_by=final_text_rendered_by, notes=notes, problems=problems, content_type=analysis.type, mood=analysis.mood)

    finally:
        if plan:
            for p in [plan.hero, plan.inset] + list(plan.extra):
                if p and p.source == 'source_post' and p.path and os.path.exists(p.path):
                    try:
                        os.remove(p.path)
                    except OSError:
                        pass
