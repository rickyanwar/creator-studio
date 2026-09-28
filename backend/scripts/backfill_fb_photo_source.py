"""Backfill publish_jobs.source_gallery_image_id for old Mode-6
(facebook_recreate) jobs whose source was NULL when they were created
(before the S9 fix).

Matching rules (per PLAN.md S9, item 3):
  - Candidate ideas: facebook_photo_ideas where
      fanpage_id == job.fanpage_id
      AND status == 'used'
      AND design_title == job.design_title   (exact string match)
  - If exactly 1 candidate: match.
  - If >1 candidate: pick the one whose used_at is closest to job.created_at.
  - If 2+ candidates are equally close (tie): mark AMBIGUOUS, skip.
  - If 0 candidates: UNMATCHED, skip.

Usage
-----
  # Dry run (default) — read-only, prints what would change:
  python backend/scripts/backfill_fb_photo_source.py

  # Apply changes:
  python backend/scripts/backfill_fb_photo_source.py --apply

  # Run inside the api container on the VPS (read-only check):
  docker exec studio_api python backend/scripts/backfill_fb_photo_source.py
"""

import argparse
import sys
from datetime import datetime, timezone
from typing import Optional


# ── Pure matching logic (importable by tests, no DB dependency) ───────────────

def match_job_to_idea(job, ideas):
    """Given a publish_job ORM object and a list of candidate
    FacebookPhotoIdea ORM objects (already filtered to the same fanpage and
    same design_title), return ``(best_idea, ambiguous_flag)``.

    * ``(None, False)``  — no candidates
    * ``(idea, False)``  — single clear winner
    * ``(idea, True)``   — >1 candidates tied for closest used_at distance
    """
    if not ideas:
        return None, False

    if len(ideas) == 1:
        return ideas[0], False

    # Pick by minimum |used_at − created_at| distance.
    job_ts = job.created_at
    if job_ts is None:
        # Can't rank without a timestamp → ambiguous
        return ideas[0], True

    def distance(idea):
        if idea.used_at is None:
            return float("inf")
        # Both columns are stored without tzinfo (UTC naive) in this codebase.
        u = idea.used_at
        c = job_ts
        return abs((u - c).total_seconds())

    sorted_ideas = sorted(ideas, key=distance)
    best_dist = distance(sorted_ideas[0])

    # Collect all ideas within the same minimum distance (ties).
    tied = [i for i in sorted_ideas if distance(i) == best_dist]
    if len(tied) > 1:
        return tied[0], True   # ambiguous

    return sorted_ideas[0], False


# ── DB-backed backfill ────────────────────────────────────────────────────────

def run_backfill(apply: bool = False) -> dict:
    """Connect to the DB and backfill.  Returns a summary dict."""
    import os, sys

    # Allow running from the repo root or the backend/ dir.
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    backend_dir = os.path.join(repo_root, "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from app.database import SessionLocal
    from app.models.publish_jobs import PublishJob, ContentType
    from app.models.facebook_photo_ideas import FacebookPhotoIdea
    from app.models.gallery import GalleryImage
    from app.services.facebook_photo_source import _MARKER_PREFIX

    db = SessionLocal()
    try:
        # All Mode-6 jobs without a source (the backfill target set).
        jobs = (
            db.query(PublishJob)
            .filter(
                PublishJob.content_type == ContentType.facebook_recreate,
                PublishJob.is_deleted == False,
                PublishJob.source_gallery_image_id == None,  # noqa: E711
            )
            .all()
        )

        matched = 0
        ambiguous = 0
        unmatched = 0
        matched_rows = []
        ambiguous_rows = []
        unmatched_rows = []

        for job in jobs:
            # All used ideas for this fanpage with the same design_title.
            candidates = (
                db.query(FacebookPhotoIdea)
                .filter(
                    FacebookPhotoIdea.fanpage_id == job.fanpage_id,
                    FacebookPhotoIdea.status == "used",
                    FacebookPhotoIdea.design_title == job.design_title,
                )
                .all()
            )

            best, is_ambiguous = match_job_to_idea(job, candidates)

            if best is None:
                unmatched += 1
                unmatched_rows.append(job.id)
            elif is_ambiguous:
                ambiguous += 1
                ambiguous_rows.append(job.id)
            else:
                matched += 1
                matched_rows.append((job.id, best.gallery_image_id))
                if apply:
                    job.source_gallery_image_id = best.gallery_image_id

        if apply:
            db.commit()

        return {
            "mode": "APPLY" if apply else "DRY-RUN",
            "total_jobs_without_source": len(jobs),
            "matched": matched,
            "ambiguous": ambiguous,
            "unmatched": unmatched,
            "matched_job_ids": matched_rows,
            "ambiguous_job_ids": ambiguous_rows,
            "unmatched_job_ids": unmatched_rows,
        }

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write changes to the DB (default: dry-run only)",
    )
    args = parser.parse_args()

    summary = run_backfill(apply=args.apply)

    mode = summary["mode"]
    print(f"\n{'=' * 60}")
    print(f"  Backfill facebook_recreate source_gallery_image_id [{mode}]")
    print(f"{'=' * 60}")
    print(f"  Jobs without source  : {summary['total_jobs_without_source']}")
    print(f"  Matched              : {summary['matched']}")
    print(f"  Ambiguous (skipped)  : {summary['ambiguous']}")
    print(f"  Unmatched (skipped)  : {summary['unmatched']}")
    if summary["matched_job_ids"]:
        print(f"\n  Matched job IDs → gallery_image_id:")
        for job_id, gi_id in summary["matched_job_ids"]:
            print(f"    job {job_id} → gallery_image {gi_id}")
    if summary["ambiguous_job_ids"]:
        print(f"\n  Ambiguous job IDs (multiple equidistant ideas — skipped):")
        for jid in summary["ambiguous_job_ids"]:
            print(f"    job {jid}")
    if summary["unmatched_job_ids"]:
        print(f"\n  Unmatched job IDs (no idea with same title — skipped):")
        for jid in summary["unmatched_job_ids"]:
            print(f"    job {jid}")
    print()
    if not args.apply:
        print("  [DRY-RUN] No changes written. Pass --apply to execute.\n")


if __name__ == "__main__":
    main()
