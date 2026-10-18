import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, status, Depends, Request
import bcrypt as _bcrypt
from jose import jwt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()
logger = logging.getLogger(__name__)

REMEMBER_ME_DAYS = 30


def _create_token(username: str, remember: bool = False) -> str:
    if remember:
        expire = datetime.now(timezone.utc) + timedelta(days=REMEMBER_ME_DAYS)
    else:
        expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {"sub": username, "exp": int(expire.timestamp())}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def _record_auth_event(db: Session, username: str, success: bool, request: Request) -> None:
    try:
        from app.models.auth_events import AuthEvent

        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent")
        if ua:
            ua = ua[:256]

        event = AuthEvent(
            username=username,
            success=success,
            ip=ip,
            user_agent=ua,
        )
        db.add(event)
        db.commit()
    except Exception as exc:
        logger.error("Failed to record auth event: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass


@router.post("/login", response_model=TokenResponse)
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    from app.models.users import User

    user = db.query(User).filter_by(username=body.username, is_active=True).first()
    if not user or not _bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
        try:
            _record_auth_event(db, body.username, success=False, request=request)
        except Exception as exc:
            logger.error("Failed to record failed auth event: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    try:
        _record_auth_event(db, body.username, success=True, request=request)
    except Exception as exc:
        logger.error("Failed to record success auth event: %s", exc)

    return TokenResponse(access_token=_create_token(body.username, remember=body.remember))
