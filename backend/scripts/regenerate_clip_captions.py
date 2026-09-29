"""Regenerate Mode-7 clip captions using the S12 one-line rules.

Usage
-----
Dry-run (default — read-only, lists which jobs would be regenerated):
    python backend/scripts/regenerate_clip_captions.py

Apply changes to the database (calls AI, writes back):
    python backend/scripts/regenerate_clip_captions.py --apply

Scope
-----
Only youtube_clip jobs with:
  • status IN (pending_design, rendering, pending_review, pending_publish)
  • is_deleted = False

Never touches published / failed / skipped jobs.

Pipeline (mirrors _consume_one exactly):
  generate_caption(_clip_caption_prompt(fanpage, idea, video))
  → strip_source_lines → clean_clip_hashtags → one_line_caption

In DRY-RUN mode the AI is NOT called — the script only lists which jobs would
be regenerated and whether idea/video resolve.  With --apply the AI is called
and the new caption is written back.

Fallback: if the idea or video cannot be resolved (or generate_caption raises),
the old caption is re-cleaned through the 3 deterministic steps and the output
notes it.
"""

from __future__ import annotations

import argparse
import sys

# ---------------------------------------------------------------------------
# Bootstrap: make sure the app package is importable when running directly
# inside the api container (cwd = /app).
# ---------------------------------------------------------------------------
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Parse args early so the script can be used in read-only mode without any
# database connection when invoked without --apply (container still on old code).
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Regenerate Mode-7 clip captions with S12 one-line rules."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Call the AI and write the new caption back to the database (default: dry-run only).",
    )
    return parser.parse_args()


REGENERABLE_STATUSES = (
    "pending_design",
    "rendering",
    "pending_review",
    "pending_publish",
)


def _line_count(text: str) -> int:
    """Number of non-empty lines in a string."""
    return sum(1 for l in text.splitlines() if l.strip())


def _resolve_idea_and_video(db, job):
    """Return (idea, video) for a job, using the correct relationship chain.

    PublishJob has no .yt_video attribute — it only has .yt_clip_idea_id and
    the denormalised string .yt_video_id.  We load idea via db.get, then video
    via idea.yt_video.  If the idea row is gone we fall back to querying
    YtVideo directly using job.fanpage_id + job.yt_video_id.
    """
    from app.models.yt_clip_ideas import YtClipIdea
    from app.models.yt_videos import YtVideo

    idea = None
    if job.yt_clip_idea_id:
        idea = db.get(YtClipIdea, job.yt_clip_idea_id)

    video = None
    if idea is not None:
        video = idea.yt_video

    if video is None and job.yt_video_id:
        video = (
            db.query(YtVideo)
            .filter_by(fanpage_id=job.fanpage_id, video_id=job.yt_video_id)
            .first()
        )

    return idea, video


def _regenerate_caption_for_job(db, job, fanpage, apply: bool):
    """Compute the new caption for one job.

    Returns (new_caption, provider, used_ai, fallback_reason).

    In DRY-RUN (apply=False) the AI is never called — returns (None, None, False, None)
    along with idea/video resolve status printed by the caller.

    In APPLY mode: calls generate_caption; on failure falls back to re-cleaning
    the existing caption through the 3 deterministic steps.
    """
    from app.services.yt_clip_title import (
        clean_clip_hashtags,
        one_line_caption,
        strip_source_lines,
    )

    idea, video = _resolve_idea_and_video(db, job)

    if not apply:
        # Dry-run: just report resolvability, no AI call
        return None, None, False, None, idea, video

    niche = None
    if fanpage:
        niche = (getattr(fanpage, "mode2_gallery_niches", None) or [None])[0] or fanpage.name
    channel_name = video.channel_name if video else None

    # Attempt AI regeneration (mirrors _consume_one exactly)
    if idea is not None and video is not None:
        try:
            from app.tasks.yt_clip import _clip_caption_prompt
            from app.services.ai_caption import generate_caption
            from app.models.publish_jobs import AIProvider

            prompt = _clip_caption_prompt(fanpage, idea, video)
            raw_caption, provider = generate_caption(prompt)

            new_caption = strip_source_lines(raw_caption.strip(), channel_name=channel_name)
            new_caption = clean_clip_hashtags(new_caption, niche=niche, channel_name=channel_name)
            new_caption = one_line_caption(new_caption)
            return new_caption, provider, True, None, idea, video

        except Exception as exc:
            fallback_reason = f"generate_caption raised: {exc}"
    else:
        parts = []
        if idea is None:
            parts.append("idea missing")
        if video is None:
            parts.append("video missing")
        fallback_reason = "; ".join(parts)

    # Fallback: re-clean the old caption through the 3 deterministic steps
    old_caption = job.ai_generated_caption or ""
    new_caption = strip_source_lines(old_caption.strip(), channel_name=channel_name)
    new_caption = clean_clip_hashtags(new_caption, niche=niche, channel_name=channel_name)
    new_caption = one_line_caption(new_caption)
    return new_caption, None, False, fallback_reason, idea, video


def main() -> None:
    args = _parse_args()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[regenerate_clip_captions] mode={mode}")
    print(f"[regenerate_clip_captions] scope: youtube_clip jobs with status in {REGENERABLE_STATUSES}, is_deleted=False")
    print()

    try:
        from app.database import SessionLocal
        from app.models.publish_jobs import PublishJob, ContentType
        from app.models.target_fanpages import TargetFanpage
        from app.services.yt_clip_title import (
            clean_clip_hashtags,
            one_line_caption,
            strip_source_lines,
        )
    except ImportError as exc:
        # Running against the old container image that does not have one_line_caption yet.
        # Fall back to a read-only listing of candidate job IDs + current line counts.
        print(f"[regenerate_clip_captions] WARNING: import failed ({exc})")
        print("[regenerate_clip_captions] Falling back to read-only job listing (no cleaning applied).")
        print()
        _fallback_listing()
        return

    db = SessionLocal()
    try:
        jobs = (
            db.query(PublishJob)
            .filter(
                PublishJob.content_type == ContentType.youtube_clip,
                PublishJob.status.in_(REGENERABLE_STATUSES),
                PublishJob.is_deleted == False,  # noqa: E712
            )
            .order_by(PublishJob.id)
            .all()
        )

        if not jobs:
            print("[regenerate_clip_captions] No eligible jobs found.")
            return

        if args.apply:
            # Apply mode: show old (first 80 chars) vs new caption in full
            print(f"{'JOB_ID':>8}  {'FP':>5}  {'STATUS':<18}  {'IDEA':>6}  {'VIDEO':>6}  {'AI':>3}  {'FALLBACK_REASON'}")
            print("-" * 90)
        else:
            # Dry-run mode: list which jobs would be regenerated and idea/video resolvability
            print(f"{'JOB_ID':>8}  {'FP':>5}  {'STATUS':<18}  {'IDEA':>6}  {'VIDEO':>6}  {'OLD_LINES':>9}")
            print("-" * 70)

        changed_count = 0
        for job in jobs:
            old_caption = job.ai_generated_caption or ""
            fanpage = db.get(TargetFanpage, job.fanpage_id)

            new_caption, provider, used_ai, fallback_reason, idea, video = _regenerate_caption_for_job(
                db, job, fanpage, apply=args.apply
            )

            idea_ok = "YES" if idea is not None else "NO"
            video_ok = "YES" if video is not None else "NO"

            if not args.apply:
                old_lines = _line_count(old_caption)
                print(
                    f"{job.id:>8}  {job.fanpage_id:>5}  {job.status:<18}  {idea_ok:>6}  {video_ok:>6}  {old_lines:>9}"
                )
                changed_count += 1  # all eligible jobs "would be regenerated"
            else:
                ai_mark = "YES" if used_ai else "no"
                fb = fallback_reason or ""
                print(
                    f"{job.id:>8}  {job.fanpage_id:>5}  {job.status:<18}  {idea_ok:>6}  {video_ok:>6}  {ai_mark:>3}  {fb}"
                )
                print(f"         OLD: {old_caption[:80]!r}")
                print(f"         NEW: {new_caption!r}")
                print()

                if new_caption and new_caption != old_caption:
                    changed_count += 1
                    job.ai_generated_caption = new_caption
                    if used_ai and provider:
                        from app.models.publish_jobs import AIProvider
                        try:
                            job.ai_provider_used = AIProvider(provider)
                        except ValueError:
                            pass  # provider string not in enum — leave existing value

        if args.apply and changed_count:
            db.commit()
            print(f"\n[regenerate_clip_captions] Committed {changed_count} updated caption(s).")
        elif args.apply:
            print(f"\n[regenerate_clip_captions] 0 captions changed. No writes made.")
        else:
            print(f"\n[regenerate_clip_captions] {len(jobs)} job(s) would be regenerated (idea/video resolve shown above). No writes made (dry-run).")

    finally:
        db.close()


def _fallback_listing() -> None:
    """Read-only fallback: list eligible job IDs + current line counts without cleaning."""
    try:
        from app.database import SessionLocal
        from app.models.publish_jobs import PublishJob, ContentType
    except ImportError as exc:
        print(f"[regenerate_clip_captions] Cannot import app models: {exc}")
        print("[regenerate_clip_captions] No jobs listed.")
        return

    db = SessionLocal()
    try:
        jobs = (
            db.query(PublishJob)
            .filter(
                PublishJob.content_type == ContentType.youtube_clip,
                PublishJob.status.in_(REGENERABLE_STATUSES),
                PublishJob.is_deleted == False,  # noqa: E712
            )
            .order_by(PublishJob.id)
            .all()
        )

        if not jobs:
            print("[regenerate_clip_captions] No eligible jobs found.")
            return

        print(f"{'JOB_ID':>8}  {'FP':>5}  {'LINES':>5}  STATUS")
        print("-" * 40)
        for job in jobs:
            caption = job.ai_generated_caption or ""
            lines = _line_count(caption)
            print(f"{job.id:>8}  {job.fanpage_id:>5}  {lines:>5}  {job.status}")

        print(f"\n[regenerate_clip_captions] {len(jobs)} eligible job(s) found (read-only listing).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
