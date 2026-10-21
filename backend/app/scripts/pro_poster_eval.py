"""Dry-run evaluation script for pro poster pipeline.

Runs compose_pro_poster in dry_run mode on specified job IDs, renders the final
card PNG through the renderer service, saves artifacts, and appends to index.json.
NEVER modifies the database or publishes jobs.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import random
import sys
import time
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.database import SessionLocal
from app.models.design_templates import DesignTemplate
from app.models.publish_jobs import PublishJob
from app.models.scraped_articles import ScrapedArticle
from app.models.target_fanpages import TargetFanpage
from app.services.design_images import resolve_template
from app.services.pro_poster import typo_background
from app.services.pro_poster.brief import brief_to_dict
from app.services.pro_poster.pipeline import compose_pro_poster
from app.services.visual_engine import VisualEngineDeferred
from app.tasks.design_renderer import render_card_png

logger = logging.getLogger(__name__)


def _photo_check_to_dict(chk: Any) -> dict[str, Any]:
    return {
        "candidate": {
            "source": chk.candidate.source,
            "gallery_image_id": chk.candidate.gallery_image_id,
            "path_or_url": chk.candidate.path_or_url,
            "role": chk.candidate.role,
        },
        "person": chk.person,
        "event_match": chk.event_match,
        "obstructed": chk.obstructed,
        "watermark": chk.watermark,
        "quality": chk.quality,
        "usable_as_hero": chk.usable_as_hero,
        "usable_as_context": chk.usable_as_context,
        "notes": chk.notes,
    }


def determine_eval_final(
    result_image: Any,
    fallback_reason: str | None,
    eval_url: str,
    live_url: str | None,
) -> tuple[str, str | None]:
    if bool(result_image):
        return "pro_poster", eval_url
    if fallback_reason in ("no verified photo", "photo check error"):
        return "typographic", eval_url
    return "production", live_url


def run_eval(
    job_ids: list[int],
    run_name: str,
    pace: float = 240.0,
    fresh_brief: bool = False,
) -> None:
    settings = get_settings()
    run_dir = Path(settings.storage_base_path) / "pro_poster_eval" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    index_file = run_dir / "index.json"

    db = SessionLocal()
    try:
        total = len(job_ids)
        for i, job_id in enumerate(job_ids):
            job = db.query(PublishJob).filter_by(id=job_id).first()
            if not job:
                print(f"Job {job_id} | not found in database")
                continue

            if fresh_brief and getattr(job, "design_analysis_json", None):
                analysis = dict(job.design_analysis_json)
                if "pro_poster_brief" in analysis:
                    del analysis["pro_poster_brief"]
                    job.design_analysis_json = analysis

            article = (
                db.query(ScrapedArticle).filter_by(id=job.source_article_id).first()
                if job.source_article_id
                else None
            )
            fanpage = (
                db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
                if job.fanpage_id
                else None
            )

            job_template = (
                db.query(DesignTemplate).filter_by(id=job.design_template_id).first()
                if job.design_template_id
                else None
            )
            category = job_template.category if job_template and job_template.category else "news"
            template = resolve_template(
                db, category, fanpage=fanpage, job_template_id=job.design_template_id
            )

            used_engine = False
            try:
                result = compose_pro_poster(db, fanpage, job, article, dry_run=True)
                used_engine = bool(result.attempts > 0 and result.engine)

                eval_url = f"/media/pro_poster_eval/{run_name}/{job_id}_new.png"
                live_url = (
                    str(getattr(job, "design_image_url", None) or "")
                    if getattr(job, "design_image_url", None)
                    else None
                )
                final, new_url = determine_eval_final(
                    result.image,
                    result.fallback_reason,
                    eval_url,
                    live_url,
                )

                # Format and render final card if not falling back to production
                if final == "pro_poster":
                    image_src = f"data:image/png;base64,{base64.b64encode(result.image).decode()}"
                    skip_face_fix = False
                    if template:
                        png_bytes = render_card_png(
                            job,
                            fanpage,
                            template,
                            image_src,
                            label=result.brief.badge_label,
                            article=article,
                            db=db,
                            skip_face_fix=skip_face_fix,
                        )
                        (run_dir / f"{job_id}_new.png").write_bytes(png_bytes)
                elif final == "typographic":
                    bg_bytes = typo_background.make_typographic_background(
                        result.brief,
                        brand_color=getattr(fanpage, "accent_color", None),
                        seed=job.id,
                    )
                    image_src = typo_background.background_datauri(bg_bytes)
                    skip_face_fix = True
                    if template:
                        png_bytes = render_card_png(
                            job,
                            fanpage,
                            template,
                            image_src,
                            label=result.brief.badge_label,
                            article=article,
                            db=db,
                            skip_face_fix=skip_face_fix,
                        )
                        (run_dir / f"{job_id}_new.png").write_bytes(png_bytes)

                # Write <job>_brief.json
                (run_dir / f"{job_id}_brief.json").write_text(
                    json.dumps(brief_to_dict(result.brief), indent=2)
                )

                # Write attempt images and build attempts_detail
                attempts_detail = []
                for att in getattr(result, "attempt_log", ()):
                    att_num = att.get("attempt")
                    att_img = att.get("image")
                    if att_img:
                        (run_dir / f"{job_id}_attempt{att_num}.png").write_bytes(att_img)
                    attempts_detail.append({
                        "attempt": att_num,
                        "engine": att.get("engine"),
                        "url": f"/media/pro_poster_eval/{run_name}/{job_id}_attempt{att_num}.png",
                        "qa": att.get("qa"),
                    })

                # Write <job>_qa.json
                qa_payload = {
                    "qa": {
                        "passed": result.qa.passed,
                        "scores": result.qa.scores,
                        "issues": list(result.qa.issues),
                        "fix_hint": result.qa.fix_hint,
                    }
                    if result.qa
                    else None,
                    "selection": {
                        "hero": _photo_check_to_dict(result.selection.hero)
                        if result.selection.hero
                        else None,
                        "context": [_photo_check_to_dict(c) for c in result.selection.context],
                        "rejected": [_photo_check_to_dict(r) for r in result.selection.rejected],
                    },
                    "engine": result.engine,
                    "attempts": result.attempts,
                    "fallback_reason": result.fallback_reason,
                    "attempts_detail": attempts_detail,
                }
                (run_dir / f"{job_id}_qa.json").write_text(json.dumps(qa_payload, indent=2))

                # Append to index.json
                try:
                    index_list = json.loads(index_file.read_text()) if index_file.exists() else []
                except Exception:
                    index_list = []

                entry = {
                    "job_id": int(getattr(job, "id", job_id)),
                    "brand": str(getattr(fanpage, "name", "") or ""),
                    "title": str(
                        getattr(job, "design_title", None)
                        or (getattr(article, "scraped_title", "") if article else "")
                    ),
                    "old_url": str(getattr(job, "design_image_url", None) or "")
                    if getattr(job, "design_image_url", None)
                    else None,
                    "new_url": new_url,
                    "final": final,
                    "layout": str(result.direction.layout if result.direction else "typographic"),
                    "engine": str(result.engine) if result.engine else None,
                    "attempts": int(result.attempts),
                    "qa": {
                        "passed": bool(result.qa.passed),
                        "scores": dict(result.qa.scores),
                        "issues": list(result.qa.issues),
                    }
                    if result.qa
                    else None,
                    "fallback_reason": str(result.fallback_reason) if result.fallback_reason else None,
                    "attempts_detail": attempts_detail,
                }
                index_list.append(entry)
                index_file.write_text(json.dumps(index_list, indent=2))

                layout_name = result.direction.layout if result.direction else "typographic"
                qa_str = "passed" if (result.qa and result.qa.passed) else ("failed" if result.qa else "none")
                print(
                    f"Job {job.id} | layout={layout_name} | engine={result.engine} | attempts={result.attempts} | qa={qa_str} | fallback={result.fallback_reason}"
                )

            except VisualEngineDeferred as exc:
                print(f"Job {job_id} | deferred until {exc.until.isoformat()} ({exc.reason})")
            except Exception as exc:
                print(f"Job {job_id} | error: {exc}")
            finally:
                # NEVER commit dry-run eval changes
                db.rollback()

            # Pacing sleep between jobs that consumed image generation
            if used_engine and i < total - 1:
                delay = pace + random.uniform(0, 60)
                time.sleep(delay)

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run evaluation for Pro Poster")
    parser.add_argument("--jobs", required=True, help="Comma-separated job IDs, e.g. 12602,12702")
    parser.add_argument("--run", required=True, help="Run name")
    parser.add_argument("--pace", type=float, default=240.0, help="Pace sleep in seconds between image generations")
    parser.add_argument("--fresh-brief", action="store_true", help="Ignore cached pro_poster_brief on jobs")
    args = parser.parse_args()

    job_ids = [int(j.strip()) for j in args.jobs.split(",") if j.strip()]
    if not job_ids:
        print("No valid job IDs provided")
        sys.exit(1)

    run_eval(job_ids, args.run, pace=args.pace, fresh_brief=args.fresh_brief)


if __name__ == "__main__":
    main()
