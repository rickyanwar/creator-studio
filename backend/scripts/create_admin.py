#!/usr/bin/env python
"""Create the initial admin user.

Usage:
    python scripts/create_admin.py --username admin --password yourpassword
"""

import argparse
import sys
import os

# Allow running from backend/ directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bcrypt as _bcrypt
from app.database import SessionLocal
from app.models.users import User


def _hash(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def main():
    parser = argparse.ArgumentParser(description="Create admin user")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        existing = db.query(User).filter_by(username=args.username).first()
        if existing:
            print(f"User '{args.username}' already exists — updating password.")
            existing.password_hash = _hash(args.password)
        else:
            user = User(
                username=args.username,
                password_hash=_hash(args.password),
                is_active=True,
            )
            db.add(user)
            print(f"Created admin user '{args.username}'")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
