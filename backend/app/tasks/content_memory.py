from app.tasks.celery_app import celery_app
from app.database import get_db
from app.models.target_fanpages import TargetFanpage
from app.services import content_memory

@celery_app.task(name="app.tasks.content_memory.refresh_content_memory")
def refresh_content_memory():
    db = next(get_db())
    try:
        fanpages = db.query(TargetFanpage).filter_by(is_active=True).all()
        for fp in fanpages:
            auto_data = content_memory.build_auto_memory(db, fp)
            content_memory.update_auto(db, fp.id, auto_data)
    finally:
        db.close()
