"""Seed (and optionally reset) the shared public-demo accounts.

Used by hosted demos (see deploy/README.md). Requires
CLAIMLAB_PUBLIC_DEMO=true and CLAIMLAB_DEMO_PASSWORD. Idempotent: creates
the two demo reviewer accounts if missing, otherwise resets their password,
role and active flag. With ``--reset`` it also deletes every case review and
evaluation run so the shared queue starts empty (run nightly from cron via
deploy/reset-demo.sh). Admin and other non-demo accounts are never touched.

Usage (from the project root, or inside the API container):
    python scripts/seed_demo.py            # seed / repair demo accounts
    python scripts/seed_demo.py --reset    # also wipe all reviews (nightly reset)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.db import SessionLocal
from backend.models import CaseReview, EvaluationRun, User
from backend.security import hash_password

DEMO_ACCOUNTS = (("demo-maker", "reviewer"), ("demo-checker", "reviewer"))
MIN_PASSWORD_LENGTH = 8


def seed_demo_accounts(db: Session, password: str) -> list[str]:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Demo password must be at least {MIN_PASSWORD_LENGTH} characters.")
    actions = []
    for username, role in DEMO_ACCOUNTS:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            db.add(User(username=username, hashed_password=hash_password(password), role=role, is_active=True))
            actions.append(f"created {username}")
        else:
            user.hashed_password = hash_password(password)
            user.role = role
            user.is_active = True
            actions.append(f"updated {username}")
    db.commit()
    return actions


def reset_demo_data(db: Session) -> tuple[int, int]:
    reviews = db.execute(delete(CaseReview)).rowcount or 0
    runs = db.execute(delete(EvaluationRun)).rowcount or 0
    db.commit()
    return reviews, runs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="Also delete all case reviews and evaluation runs")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.public_demo:
        print("CLAIMLAB_PUBLIC_DEMO is not enabled; nothing to do.")
        return 0
    if not settings.demo_password:
        print("CLAIMLAB_DEMO_PASSWORD is not set; refusing to seed demo accounts.", file=sys.stderr)
        return 1
    db = SessionLocal()
    try:
        if args.reset:
            reviews, runs = reset_demo_data(db)
            print(f"Reset demo data: deleted {reviews} case review(s) and {runs} evaluation run(s).")
        print("Demo accounts: " + ", ".join(seed_demo_accounts(db, settings.demo_password)) + ".")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
