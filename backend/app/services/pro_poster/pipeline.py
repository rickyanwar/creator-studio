"""Pro poster pipeline orchestration.

Coordinates brief building, photo selection, art direction, generation via
visual_engine (ChatGPT/Codex), vision QA review, and fallback decisions.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.settings import Settings
from app.services import speaker_design
from app.services.gptcli_client import GptcliError
from app.services.pro_poster import art_direction, concept as concept_mod, photos, qa
from app.services.pro_poster.brief import brief_from_dict, brief_to_dict, build_brief
from app.services.pro_poster.types import PosterBrief, PosterConcept, ProPosterResult
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
}


def pro_poster_applies(db: Session, fanpage: Any) -> bool:
    """Check if pro poster pipeline is enabled globally and for this fanpage."""
    s = db.query(Settings).first()
    if not s or not getattr(s, "pro_poster_enabled", False):
        return False
    allowed_ids = getattr(s, "pro_poster_fanpage_ids", None) or []
    if not allowed_ids:
        return True
    return fanpage is not None and getattr(fanpage, "id", None) in allowed_ids


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
    return {
        "attempt": attempt,
        "engine": engine or "",
        "qa": {
            "passed": bool(qa_res.passed),
            "scores": dict(qa_res.scores) if getattr(qa_res, "scores", None) else {},
            "issues": list(qa_res.issues) if getattr(qa_res, "issues", None) else [],
            "fix_hint": str(getattr(qa_res, "fix_hint", "") or ""),
        },
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
    4. Art direct composition (or fallback to typographic if no photo)
    5. Generate card via visual_engine (with deferral propagation)
    6. Run safety QA checks (identity + event)
    7. Log summary line and return result
    """
    settings = get_settings()

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
    direction = art_direction.direct(
        brief,
        selection,
        headline=headline,
        story_text=story_text,
        concept=concept,
    )
    if direction.layout == "typographic":
        photo_errors = sum(1 for c in selection.rejected if c.person == "error")
        reason = "photo check error" if photo_errors else "no verified photo"
        res = ProPosterResult(
            image=None,
            brief=brief,
            selection=selection,
            direction=direction,
            qa=None,
            engine=None,
            attempts=0,
            fallback_reason=reason,
        )
        _log_job_summary(job, res)
        return res

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
    hero_bytes = _read_hero_ref_bytes(selection)
    qa_res = qa.check_safety(
        image,
        brief,
        hero_ref_bytes=hero_bytes,
        headline=headline,
    )
    attempt_log.append(
        _make_attempt_entry(attempts, current_engine, qa_res, image, concept=concept)
    )

    if not qa_res.passed:
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
    _log_job_summary(job, res)
    return res
