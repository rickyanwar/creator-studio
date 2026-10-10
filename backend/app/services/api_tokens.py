import hashlib
import secrets
from typing import Tuple, List, Optional
from sqlalchemy.orm import Session
from app.models.api_tokens import ApiToken

VALID_SCOPES = ("read", "memory:write", "recommendations:write")

def hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()

def create_token(db: Session, name: str, scopes: List[str]) -> Tuple[ApiToken, str]:
    # Validate scopes
    for scope in scopes:
        if scope not in VALID_SCOPES:
            raise ValueError(f"Invalid scope: {scope}")
    
    plaintext = "hst_" + secrets.token_urlsafe(32)
    token_hash = hash_token(plaintext)
    
    db_token = ApiToken(
        name=name,
        token_hash=token_hash,
        scopes=scopes
    )
    db.add(db_token)
    db.commit()
    db.refresh(db_token)
    return db_token, plaintext

def verify(db: Session, plaintext: str) -> Optional[ApiToken]:
    token_hash = hash_token(plaintext)
    token = db.query(ApiToken).filter(ApiToken.token_hash == token_hash).first()
    if token and not token.revoked_at:
        return token
    return None
