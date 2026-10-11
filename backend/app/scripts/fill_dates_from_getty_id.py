import argparse
from collections import Counter

from app.database import SessionLocal
from app.models.gallery import GalleryImage
from app.services.getty_dates import anchors_from_db, build_hints, estimate_date_from_anchors, parse_asset_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Estimate missing Getty capture dates from asset IDs")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    db = SessionLocal()
    counts = Counter()
    try:
        anchors = anchors_from_db(db)
        query = db.query(GalleryImage).filter(GalleryImage.captured_at.is_(None))
        if args.limit:
            query = query.limit(args.limit)
        for row in query.yield_per(500):
            asset_id = parse_asset_id(row.source_image_url)
            if asset_id is None:
                continue
            estimate = estimate_date_from_anchors(asset_id, anchors)
            if not estimate:
                continue
            captured_at, confidence = estimate
            counts[confidence] += 1
            if not args.dry_run:
                attributes = dict(row.attributes or {})
                attributes["captured_at_confidence"] = confidence
                attributes["captured_at_source"] = "getty_id"
                attributes["getty_hints"] = build_hints(row.source_image_url)
                row.captured_at = captured_at
                row.attributes = attributes
        if not args.dry_run:
            db.commit()
        print(f"day: {counts['day']}, estimated: {counts['estimated']}, total: {sum(counts.values())}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
