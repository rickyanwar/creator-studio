from app.database import SessionLocal
from app.services.gallery_link_import import import_links
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.gallery_link_import.import_gallery_links")
def import_gallery_links(links: list[str], keyword: str | None = None, hints: dict | None = None) -> dict:
    db = SessionLocal()
    try:
        results = import_links(db, links, keyword, hints)
        return {"results": [result.__dict__ for result in results]}
    finally:
        db.close()
