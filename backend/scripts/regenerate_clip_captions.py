"""Regenerate Mode-7 clip captions using the S12 one-line rules.

Usage
-----
Dry-run (default — read-only, prints old/new per job):
    python backend/scripts/regenerate_clip_captions.py

Apply changes to the database:
    python backend/scripts/regenerate_clip_captions.py --apply

Scope
-----
Only youtube_clip jobs with:
  • status IN (pending_design, rendering, pending_review, pending_publish)
  • is_deleted = False

Never touches published / failed / skipped jobs.

Pipeline (mirrors _consume_one exactly):
  strip_source_lines → clean_clip_hashtags → one_line_caption

The AI caption call is NOT made here — instead we re-clean the existing
ai_generated_caption through the same 3 deterministic steps.  This is safe
and fast for a dry-run listing.  When --apply is given the cleaned caption
is written back to ai_generated_caption.
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
        help="Write the cleaned caption back to the database (default: dry-run only).",
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

        print(f"{'JOB_ID':>8}  {'FP':>5}  {'OLD_LINES':>9}  {'NEW_LINES':>9}  {'CHANGED':>7}  STATUS")
        print("-" * 70)

        changed_count = 0
        for job in jobs:
            old_caption = job.ai_generated_caption or ""

            # Fetch niche and channel name for cleaning.
            fanpage = db.get(TargetFanpage, job.fanpage_id)
            niche = None
            if fanpage:
                niche = (getattr(fanpage, "mode2_gallery_niches", None) or [None])[0] or fanpage.name
            channel_name = None
            if job.yt_video:
                channel_name = getattr(job.yt_video, "channel_name", None)

            # Apply the 3 cleaning steps.
            new_caption = strip_source_lines(old_caption.strip(), channel_name=channel_name)
            new_caption = clean_clip_hashtags(new_caption, niche=niche, channel_name=channel_name)
            new_caption = one_line_caption(new_caption)

            old_lines = _line_count(old_caption)
            new_lines = _line_count(new_caption)
            changed = old_caption != new_caption

            if changed:
                changed_count += 1

            print(
                f"{job.id:>8}  {job.fanpage_id:>5}  {old_lines:>9}  {new_lines:>9}  "
                f"{'YES' if changed else 'no':>7}  {job.status}"
            )

            if args.apply and changed:
                job.ai_generated_caption = new_caption

        if args.apply and changed_count:
            db.commit()
            print(f"\n[regenerate_clip_captions] Committed {changed_count} updated caption(s).")
        else:
            print(f"\n[regenerate_clip_captions] {changed_count} job(s) would change. No writes made (dry-run).")

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
