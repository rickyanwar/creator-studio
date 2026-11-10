"""Pro poster pipeline orchestration.

Coordinates brief building, photo selection, art direction, generation via
visual_engine (ChatGPT/Codex), vision QA review, and fallback decisions.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.settings import Settings
from app.services import speaker_design
from app.services.gptcli_client import GptcliError
from app.services.pro_poster import art_direction, concept as concept_mod, photos, qa
from app.services.pro_poster.brief import brief_from_dict, brief_to_dict, build_brief
from app.services.pro_poster.types import (
    ArtDirection, PhotoSelection, PosterBrief, PosterConcept, ProPosterResult, Subject,
)
from app.services.qa_toggles import qa_enabled
from app.services.visual_engine import (
    GenerationRequest,
    NoAllowedEngine,
    VisualEngineDeferred,
    VisualEngineError,
    generate_ex,
)

logger = logging.getLogger(__name__)

_LAYOUT_MAP: dict[str, str] = {
    "hero_with_context": "inset",
    "hero_solo": "solo",
    "action_full_bleed": "action",
    "scene": "action",
}


def promote_secondary(brief: PosterBrief) -> PosterBrief | None:
    """A: the hero has no verified photo -> make the first secondary subject the poster person."""
    hero = next((s for s in brief.subjects if s.role == "hero"), None)
    secondary = next((s for s in brief.subjects if s.role == "secondary"), None)
    if hero is None or secondary is None:
        return None
    return replace(brief, subjects=(
        Subject(name=secondary.name, role="hero", team=secondary.team),
        Subject(name=hero.name, role="mentioned", team=hero.team),
        *(s for s in brief.subjects if s is not hero and s is not secondary),
    ))


def pro_poster_applies(db: Session, fanpage: Any) -> bool:
    """Check if pro poster pipeline is enabled globally and for this fanpage."""
    s = db.query(Settings).first()
    if not s or not getattr(s, "pro_poster_enabled", False):
        return False
    allowed_ids = getattr(s, "pro_poster_fanpage_ids", None) or []
    if not allowed_ids:
        return True
    fanpage_id = getattr(fanpage, "id", None) if fanpage is not None else None
    if fanpage_id is None:
        return False
    if fanpage_id in allowed_ids:
        return True
    # A group lane uses the Pro poster when its brand does.
    from app.services.fb_share.lanes import lane_brand_id
    return lane_brand_id(db, fanpage_id) in allowed_ids


def _log_job_summary(job: Any, res: ProPosterResult) -> None:
    job_id = getattr(job, "id", None)
    layout = res.direction.layout if res.direction else "unknown"
    if res.selection and res.selection.hero:
        cand = res.selection.hero.candidate
        hero_str = f"gallery:{cand.gallery_image_id}" if cand.source == "gallery" else "story"
    else:
        hero_str = "none"
    scores = res.qa.scores if res.qa else {}
    logger.info(
        "ProPoster job=%s layout=%s hero=%s engine=%s attempts=%d qa=%s fallback=%s",
        job_id,
        layout,
        hero_str,
        res.engine,
        res.attempts,
        scores,
        res.fallback_reason,
    )


def _make_attempt_entry(
    attempt: int,
    engine: str | None,
    qa_res: Any,
    image: bytes,
    concept: PosterConcept | None = None,
) -> dict[str, Any]:
    concept_dict = None
    if concept is not None:
        concept_dict = {
            "key_moment": concept.key_moment,
            "scene": concept.scene,
            "inset": concept.inset,
            "graphic_words": list(concept.graphic_words),
            "palette": concept.palette,
            "mood": concept.mood,
        }
    qa_dict = None
    if qa_res is not None:
        qa_dict = {
            "passed": bool(qa_res.passed),
            "scores": dict(qa_res.scores) if getattr(qa_res, "scores", None) else {},
            "issues": list(qa_res.issues) if getattr(qa_res, "issues", None) else [],
            "fix_hint": str(getattr(qa_res, "fix_hint", "") or ""),
        }
    return {
        "attempt": attempt,
        "engine": engine or "",
        "qa": qa_dict,
        "image": image,
        "concept": concept_dict,
    }


def _read_hero_ref_bytes(selection: Any) -> bytes | None:
    if not selection:
        return None
    for ref_check in (
        getattr(selection, "portrait", None),
        getattr(selection, "hero", None),
        getattr(selection, "face_ref", None),
    ):
        if not ref_check or not ref_check.candidate:
            continue
        path = ref_check.candidate.path_or_url
        if not path:
            continue
        try:
            p = Path(path)
            if p.is_file():
                return p.read_bytes()
        except Exception:
            pass
    return None


def _headline(job: Any, article: Any) -> str | None:
    raw = None
    if job and getattr(job, "design_title", None):
        raw = job.design_title
    elif article and getattr(article, "scraped_title", None):
        raw = article.scraped_title
    if raw is None:
        return None
    return raw.replace("**", "")


def _story_text(article: Any) -> str | None:
    if article and getattr(article, "scraped_content", None):
        return article.scraped_content
    return None


def _story_for_photos(headline: str | None, story_text: str | None) -> str:
    parts: list[str] = []
    if isinstance(headline, str) and headline.strip():
        parts.append(headline.strip())
    if isinstance(story_text, str) and story_text.strip():
        parts.append(story_text.strip()[:600])
    return " — ".join(parts) if parts else ""


_REUSE_FORMAT = "pro_poster"
_REUSED_FROM_KEY = "pro_poster_reused_from"  # = ai_image_reuse.PRO_POSTER_REUSED_FROM_KEY


def _article_id(job: Any, article: Any) -> int | None:
    return getattr(article, "id", None) or getattr(job, "source_article_id", None)


def _reuse_sibling_poster(db: Session, fanpage: Any, job: Any, article: Any) -> ProPosterResult | None:
    """Edit once: reuse the AI poster image a sibling fanpage already made for this article.

    The artwork carries no headline (the page template adds it), so the same article gives
    the same image; only the caption and the template text differ.
    """
    from app.models.publish_jobs import PublishJob
    from app.services import ai_image_reuse

    article_id = _article_id(job, article)
    if not article_id or getattr(job, "id", None) is None:
        return None
    analysis = dict(getattr(job, "design_analysis_json", None) or {})
    if _REUSED_FROM_KEY in analysis:  # stale from an earlier render
        analysis.pop(_REUSED_FROM_KEY)
        job.design_analysis_json = analysis
    source_key = ai_image_reuse.news_source_key(article_id)
    reusable = ai_image_reuse.find_reusable(
        db, job, source_key=source_key, format_key=_REUSE_FORMAT, prompt_text="",
        qa_required=qa_enabled(fanpage, "news"),
    )
    if reusable is None:
        return None
    source = db.query(PublishJob).filter_by(id=reusable.job_id).first()
    brief_data = (getattr(source, "design_analysis_json", None) or {}).get("pro_poster_brief")
    if not brief_data:
        return None
    try:
        image = Path(reusable.path).read_bytes()
        own_path = ai_image_reuse.copy_reused(reusable.path, job)
    except OSError as exc:
        logger.warning("ProPoster job=%s could not reuse job %s image: %s", job.id, reusable.job_id, exc)
        return None
    brief = brief_from_dict(brief_data, job_id=job.id)
    engine = reusable.record.get("engine")
    layout = reusable.record.get("layout") or "hero_solo"
    job.design_analysis_json = {
        **(job.design_analysis_json or {}),
        "pro_poster_brief": brief_to_dict(brief),
        _REUSED_FROM_KEY: reusable.job_id,
    }
    ai_image_reuse.remember(
        job, own_path, source_key=source_key, format_key=_REUSE_FORMAT, prompt_text="",
        engine=engine, qa_checked=bool(reusable.record.get("qa_checked")), layout=layout, reused_from=reusable,
    )
    res = ProPosterResult(
        image=image,
        brief=brief,
        selection=PhotoSelection(hero=None),
        direction=ArtDirection(layout=layout, prompt="", refs=()),
        qa=None,
        engine=engine,
        attempts=0,
        fallback_reason=None,
    )
    logger.info("ProPoster job=%s reused the AI image of job %s", job.id, reusable.job_id)
    return res


def _remember_poster(db: Session, fanpage: Any, job: Any, article: Any, image: bytes,
                     *, engine: str | None, qa_checked: bool, layout: str) -> None:
    """Keep the raw AI poster on disk so sibling fanpages can reuse it (edit once)."""
    from app.services import ai_image_reuse

    article_id = _article_id(job, article)
    if not article_id or getattr(job, "id", None) is None or fanpage is None:
        return
    if not ai_image_reuse.has_siblings(db, fanpage.id):
        return
    try:
        path = ai_image_reuse.save_ai_image(job, image)
    except OSError as exc:
        logger.warning("ProPoster job=%s could not keep the AI image for reuse: %s", job.id, exc)
        return
    ai_image_reuse.remember(
        job, path, source_key=ai_image_reuse.news_source_key(article_id), format_key=_REUSE_FORMAT,
        prompt_text="", engine=engine, qa_checked=qa_checked, layout=layout,
    )


def compose_pro_poster(
    db: Session,
    fanpage: Any,
    job: Any,
    article: Any,
    *,
    dry_run: bool = False,
) -> ProPosterResult:
    """Compose a professional editorial poster for news articles.

    Steps:
    1. Build or reuse brief from job.design_analysis_json
    2. Fetch and persist article story photo if available
    3. Select verified hero and context photos
    4. Art direct composition (a scene if no photo)
    5. Generate card via visual_engine (with deferral propagation)
    6. Run safety QA checks (identity + event)
    7. Log summary line and return result
    """
    settings = get_settings()

    # 0. Edit once: a sibling fanpage already made the AI image for this article.
    if not dry_run and job:
        reused = _reuse_sibling_poster(db, fanpage, job, article)
        if reused is not None:
            return reused

    # 1. Brief
    brief_data = (
        (job.design_analysis_json or {}).get("pro_poster_brief")
        if job and getattr(job, "design_analysis_json", None)
        else None
    )
    if brief_data:
        brief: PosterBrief = brief_from_dict(brief_data, job_id=getattr(job, "id", 0))
    else:
        brief = build_brief(job, article, fanpage)
        if not dry_run and job:
            analysis = dict(getattr(job, "design_analysis_json", None) or {})
            analysis["pro_poster_brief"] = brief_to_dict(brief)
            job.design_analysis_json = analysis

    # 2. Story photo
    story_photo_path: str | None = None
    try:
        from app.services import story_photo
        sp_bytes = story_photo.fetch_story_photo(article)
        if sp_bytes:
            designs_dir = Path(settings.storage_base_path) / "designs"
            designs_dir.mkdir(parents=True, exist_ok=True)
            ext = "png" if sp_bytes.startswith(b"\x89PNG") else "jpg"
            target_file = designs_dir / f"pro_poster_story_{getattr(job, 'id', 0)}.{ext}"
            target_file.write_bytes(sp_bytes)
            story_photo_path = str(target_file)
    except Exception as exc:
        logger.warning("Failed to fetch story photo for job %s: %s", getattr(job, "id", None), exc)

    headline = _headline(job, article)
    story_text = _story_text(article)
    story = _story_for_photos(headline, story_text)

    # 3. Photo selection
    selection = photos.select_photos(
        db, brief, story_photo_path=story_photo_path, story=story
    )
    promoted_from: str | None = None
    extra_photo_errors = 0
    first_photo_errors = sum(1 for c in selection.rejected if c.person == "error")
    if not dry_run and job and (getattr(job, "design_analysis_json", None) or {}).get("pro_poster_promoted"):
        analysis = dict(job.design_analysis_json)
        analysis.pop("pro_poster_promoted", None)  # stale from an earlier render
        job.design_analysis_json = analysis
    # Only when the hero truly has no verified photo: a vision error means retry later, not swap people.
    if selection.hero is None and selection.face_ref is None and not first_photo_errors:
        promoted = promote_secondary(brief)
        if promoted is not None:
            alt = photos.select_photos(db, promoted, story_photo_path=story_photo_path, story=story)
            if alt.hero is not None or alt.face_ref is not None:
                promoted_from = next(s.name for s in brief.subjects if s.role == "hero")
                promoted_to = promoted.subjects[0].name
                logger.info(
                    "ProPoster job=%s no verified photo of %s, using secondary subject %s",
                    getattr(job, "id", None), promoted_from, promoted_to,
                )
                brief, selection = promoted, alt
                if not dry_run and job:
                    analysis = dict(getattr(job, "design_analysis_json", None) or {})
                    analysis["pro_poster_promoted"] = {"from": promoted_from, "to": promoted_to}
                    job.design_analysis_json = analysis
            else:
                extra_photo_errors = sum(1 for c in alt.rejected if c.person == "error")

    concept: PosterConcept | None = None
    try:
        concept = concept_mod.build_concept(
            brief, selection, headline=headline, story_text=story_text
        )
    except Exception as exc:
        logger.warning(
            "Failed to build poster concept for job %s: %s",
            getattr(job, "id", None),
            exc,
        )
        concept = None

    # 4. Art direction
    layout = art_direction.choose_layout(brief, selection)
    photo_errors = (
        sum(1 for c in selection.rejected if c.person == "error") if promoted_from else first_photo_errors
    ) + extra_photo_errors
    if layout == "scene" and photo_errors:
        # 9Router vision errors: let design_renderer retry later instead of generating a person-less scene
        res = ProPosterResult(
            image=None,
            brief=brief,
            selection=selection,
            direction=ArtDirection(layout="typographic", prompt="", refs=(), allowed_text=()),
            qa=None,
            engine=None,
            attempts=0,
            fallback_reason="photo check error",
        )
        _log_job_summary(job, res)
        return res
    direction = art_direction.direct(
        brief,
        selection,
        headline=headline,
        story_text=story_text,
        concept=concept,
        absent_hero=promoted_from,
    )

    # 5. Generation request
    gen_layout = _LAYOUT_MAP.get(direction.layout, "inset")
    req = GenerationRequest(
        layout=gen_layout,  # type: ignore[arg-type]
        refs=direction.refs,
        prompt=direction.prompt,
        size=direction.size,
    )
    attempts = 0
    attempt_log: list[dict] = []
    fanpage_id = getattr(fanpage, "id", None) if fanpage else None

    # Let VisualEngineDeferred propagate to caller
    try:
        gen_res = generate_ex(db, req, fanpage_id=fanpage_id)
    except NoAllowedEngine:
        # No engine could run (ChatGPT and Codex both unavailable), so nothing
        # was generated. attempts=0 lets the caller fall back to the regular speaker design.
        res = ProPosterResult(
            image=None,
            brief=brief,
            selection=selection,
            direction=direction,
            qa=None,
            engine=None,
            attempts=0,
            fallback_reason="engine: chatgpt not available",
            attempt_log=(),
        )
        _log_job_summary(job, res)
        return res
    except (VisualEngineError, GptcliError) as exc:
        kind = getattr(exc, "kind", None) or getattr(exc, "type", "error")
        res = ProPosterResult(
            image=None,
            brief=brief,
            selection=selection,
            direction=direction,
            qa=None,
            engine=None,
            attempts=1,
            fallback_reason=f"engine: {kind}",
            attempt_log=(),
        )
        _log_job_summary(job, res)
        return res

    attempts = 1
    current_engine = gen_res.engine
    image = speaker_design._finish(gen_res.image)
    if qa_enabled(fanpage, "news"):
        hero_bytes = _read_hero_ref_bytes(selection)
        qa_res = qa.check_safety(
            image,
            brief,
            hero_ref_bytes=hero_bytes,
            headline=headline,
        )
    else:
        qa_res = None
    attempt_log.append(
        _make_attempt_entry(attempts, current_engine, qa_res, image, concept=concept)
    )

    if qa_res is not None and not qa_res.passed:
        first_issue = qa_res.issues[0] if qa_res.issues else "safety check failed"
        res = ProPosterResult(
            image=None,
            brief=brief,
            selection=selection,
            direction=direction,
            qa=qa_res,
            engine=current_engine,
            attempts=1,
            fallback_reason=f"qa: {first_issue}",
            attempt_log=tuple(attempt_log),
        )
        _log_job_summary(job, res)
        return res

    res = ProPosterResult(
        image=image,
        brief=brief,
        selection=selection,
        direction=direction,
        qa=qa_res,
        engine=current_engine,
        attempts=1,
        fallback_reason=None,
        attempt_log=tuple(attempt_log),
    )
    if not dry_run and job and image:
        _remember_poster(db, fanpage, job, article, image, engine=current_engine,
                         qa_checked=qa_res is not None, layout=direction.layout)
    _log_job_summary(job, res)
    return res
