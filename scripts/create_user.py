"""Provision a reviewer/admin account (self-registration is disabled by
design — see backend.config.Settings.allow_self_registration). Run this
once per account from an operator shell that has access to the target
database; it prompts for a password rather than accepting it as a CLI
argument, so it does not end up in shell history.

Usage (from the project root):
    python scripts/create_user.py --username alice --role reviewer
    python scripts/create_user.py --username admin --role admin
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import SessionLocal
from backend.models import User
from backend.security import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=["reviewer", "admin"], default="reviewer")
    args = parser.parse_args()

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords did not match.")
    if len(password) < 12:
        raise SystemExit("Use a password of at least 12 characters.")

    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == args.username).first():
            raise SystemExit(f"User '{args.username}' already exists.")
        user = User(
            username=args.username,
            hashed_password=hash_password(password),
            role=args.role,
            is_active=True,
        )
        db.add(user)
        db.commit()
        print(f"Created {args.role} account '{args.username}'.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
