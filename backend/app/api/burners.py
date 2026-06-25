from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DB
from app.schemas.burner import BurnerCreate, BurnerUpdate, BurnerOut, BurnerChallengeOTP

router = APIRouter(prefix="/burners", tags=["burners"])

MAX_BURNERS = 5


@router.get("", response_model=list[BurnerOut])
def list_burners(db: DB, _: CurrentUser):
    from app.models.burner_accounts import BurnerAccount
    return db.query(BurnerAccount).order_by(BurnerAccount.id).all()


@router.post("", response_model=BurnerOut, status_code=status.HTTP_201_CREATED)
def create_burner(body: BurnerCreate, db: DB, _: CurrentUser):
    from app.models.burner_accounts import BurnerAccount
    from app.services.encryption import encrypt

    count = db.query(BurnerAccount).count()
    if count >= MAX_BURNERS:
        raise HTTPException(status_code=400, detail=f"Max {MAX_BURNERS} burner accounts allowed")

    existing = db.query(BurnerAccount).filter_by(ig_username=body.ig_username).first()
    if existing:
        raise HTTPException(status_code=409, detail="Burner with this username already exists")

    burner = BurnerAccount(
        ig_username=body.ig_username,
        encrypted_password=encrypt(body.password),
        proxy_url=body.proxy_url,
    )
    db.add(burner)
    db.commit()
    db.refresh(burner)
    return burner


@router.put("/{burner_id}", response_model=BurnerOut)
def update_burner(burner_id: int, body: BurnerUpdate, db: DB, _: CurrentUser):
    from app.models.burner_accounts import BurnerAccount

    burner = db.query(BurnerAccount).filter_by(id=burner_id).first()
    if not burner:
        raise HTTPException(status_code=404, detail="Burner not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(burner, field, value)

    db.commit()
    db.refresh(burner)
    return burner


@router.delete("/{burner_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_burner(burner_id: int, db: DB, _: CurrentUser):
    from app.models.burner_accounts import BurnerAccount

    burner = db.query(BurnerAccount).filter_by(id=burner_id).first()
    if not burner:
        raise HTTPException(status_code=404, detail="Burner not found")

    db.delete(burner)
    db.commit()


@router.post("/{burner_id}/challenge")
def submit_otp(burner_id: int, body: BurnerChallengeOTP, db: DB, _: CurrentUser):
    """Submit OTP for a challenged burner account."""
    from app.models.burner_accounts import BurnerAccount, BurnerStatus
    from app.services.ig_session_manager import IGSessionManager

    burner = db.query(BurnerAccount).filter_by(id=burner_id).first()
    if not burner:
        raise HTTPException(status_code=404, detail="Burner not found")

    if burner.status != BurnerStatus.challenged:
        raise HTTPException(status_code=400, detail="Burner is not in challenged state")

    manager = IGSessionManager(burner, db)
    try:
        cl = manager.get_client()
        cl.challenge_resolve(body.otp_code)
        burner.status = BurnerStatus.active
        burner.last_error = None
        db.commit()
        return {"ok": True, "message": "OTP accepted, burner active"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"OTP failed: {exc}")


@router.post("/{burner_id}/test-session")
def test_session(burner_id: int, db: DB, _: CurrentUser):
    """Test that the burner session is still valid."""
    from app.models.burner_accounts import BurnerAccount
    from app.services.ig_session_manager import IGSessionManager

    burner = db.query(BurnerAccount).filter_by(id=burner_id).first()
    if not burner:
        raise HTTPException(status_code=404, detail="Burner not found")

    manager = IGSessionManager(burner, db)
    try:
        cl = manager.get_client()
        # Try a lightweight call
        account_info = cl.account_info()
        return {"ok": True, "ig_username": account_info.username}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Session test failed: {exc}")


@router.post("/{burner_id}/post-story-now")
def post_story_now(burner_id: int, db: DB, _: CurrentUser):
    """Manually trigger a story post for a burner (for testing)."""
    from app.models.burner_accounts import BurnerAccount
    from app.tasks.story_poster import post_single_story

    burner = db.query(BurnerAccount).filter_by(id=burner_id).first()
    if not burner:
        raise HTTPException(status_code=404, detail="Burner not found")

    task = post_single_story.delay(burner_id)
    return {"ok": True, "task_id": task.id}


@router.post("/{burner_id}/post-comment-now")
def post_comment_now(burner_id: int, db: DB, _: CurrentUser):
    """Manually trigger a comment post for a burner (for testing)."""
    from app.models.burner_accounts import BurnerAccount
    from app.tasks.comment_poster import post_single_comment

    burner = db.query(BurnerAccount).filter_by(id=burner_id).first()
    if not burner:
        raise HTTPException(status_code=404, detail="Burner not found")

    task = post_single_comment.delay(burner_id)
    return {"ok": True, "task_id": task.id}


@router.post("/{burner_id}/import-session")
def import_session(burner_id: int, body: dict, db: DB, _: CurrentUser):
    """Import a pre-existing instagrapi session JSON (exported from a local login)."""
    from app.models.burner_accounts import BurnerAccount, BurnerStatus
    from app.services.encryption import encrypt
    from instagrapi import Client
    import json

    burner = db.query(BurnerAccount).filter_by(id=burner_id).first()
    if not burner:
        raise HTTPException(status_code=404, detail="Burner not found")

    try:
        cl = Client()
        if burner.proxy_url:
            cl.set_proxy(burner.proxy_url)
        cl.set_settings(body)
        # Verify the session is actually valid
        account_info = cl.account_info()
        # Save the session
        burner.encrypted_session = encrypt(json.dumps(body))
        burner.status = BurnerStatus.active
        burner.last_error = None
        db.commit()
        return {"ok": True, "ig_username": account_info.username}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Session import failed: {exc}")
