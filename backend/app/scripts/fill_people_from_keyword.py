import argparse

from app.database import SessionLocal
from app.services.muse_labeler import fill_people_from_keyword


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill empty gallery people labels from gallery keywords")
    parser.add_argument("--dry-run", action="store_true", help="report eligible rows without changing them")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        count = fill_people_from_keyword(db, dry_run=args.dry_run)
        mode = "would update" if args.dry_run else "updated"
        print(f"{mode}: {count}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
