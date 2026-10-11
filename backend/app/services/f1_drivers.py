from __future__ import annotations
from sqlalchemy.orm import Session
from sqlalchemy import or_, func, desc
from app.models.f1_drivers import F1Driver


def get_driver(db: Session, surname_or_name: str, season: int | None = None) -> F1Driver | None:
    """Return the F1Driver whose surname or full_name matches *surname_or_name*
    (case-insensitive).  When *surname_or_name* is a multi-word string (e.g.
    "Carlos Sainz") and no exact full-name match exists, each word is tried as
    a surname so that seed rows that have only a surname (full_name=NULL) are
    still found.  Among several matching rows the highest-season row wins (most
    current data)."""
    base = db.query(F1Driver)
    if season is not None:
        base = base.filter(F1Driver.season == season)

    search_term = surname_or_name.strip()

    # 1. Exact match on full_name or surname (original behaviour).
    row = base.filter(
        or_(
            func.lower(F1Driver.surname) == func.lower(search_term),
            func.lower(F1Driver.full_name) == func.lower(search_term),
        )
    ).order_by(desc(F1Driver.season)).first()
    if row:
        return row

    # 2. If the input has multiple words, try each word as a surname.
    words = [w for w in search_term.split() if len(w) > 1]
    if len(words) > 1:
        for word in words:
            row = base.filter(
                func.lower(F1Driver.surname) == func.lower(word)
            ).order_by(desc(F1Driver.season)).first()
            if row:
                return row

    return None
