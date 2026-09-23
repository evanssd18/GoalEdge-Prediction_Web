"""One-off: create (or promote) the administrator account.

Run from the backend directory:

    .\\.venv\\Scripts\\python.exe _make_admin.py

Uses the app's own hash_password() so the stored hash matches what
/auth/login verifies against -- writing the row by hand would produce a
hash the server silently refuses.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import User
from app.security import hash_password

EMAIL = "evansantwi010@gmail.com"
USERNAME = "evansantwi010"
PASSWORD = "Disway@18"


def main() -> None:
    db = SessionLocal()
    try:
        user = db.scalars(select(User).where(User.email == EMAIL)).first()

        if user is None:
            # The username column is unique too, so a different account holding
            # this username has to be moved rather than collided with.
            clash = db.scalars(select(User).where(User.username == USERNAME)).first()
            if clash is not None:
                clash.username = f"{USERNAME}_{clash.id}"

            user = User(
                email=EMAIL,
                username=USERNAME,
                hashed_password=hash_password(PASSWORD),
                is_admin=True,
                is_premium=True,
                signup_ip="127.0.0.1",
                last_ip="127.0.0.1",
                last_login_at=datetime.now(timezone.utc),
                last_seen_at=datetime.now(timezone.utc),
                login_count=0,
            )
            db.add(user)
            action = "created"
        else:
            user.hashed_password = hash_password(PASSWORD)
            user.is_admin = True
            user.is_premium = True
            user.status = "active"
            user.status_reason = None
            action = "updated"

        db.commit()
        db.refresh(user)

        total_admins = len(db.scalars(select(User).where(User.is_admin.is_(True))).all())
        print(f"{action}: id={user.id} email={user.email} username={user.username}")
        print(f"is_admin={user.is_admin} status={user.status} total_admins={total_admins}")

        # Confirm the stored hash verifies, so a typo in the password would be
        # caught here rather than at the sign-in screen.
        from app.security import verify_password

        print(f"password_verifies={verify_password(PASSWORD, user.hashed_password)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
