from __future__ import annotations

import hmac
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from app.config import settings

ALGORITHM = "HS256"


def _pwh_snapshot(password_hash: str) -> str:
    """Derive a short, non-reversible snapshot from a password hash for reset token binding."""
    return hmac.new(
        settings.SECRET_KEY.encode(), password_hash.encode(), hashlib.sha256,
    ).hexdigest()[:16]


def create_access_token(user_id: str, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_reset_token(email: str, pwh_snapshot: str) -> str:
    """Reset token valid 15 min. pwh_snapshot invalidates it after password change."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    payload = {
        "email": email,
        "pwh": _pwh_snapshot(pwh_snapshot),
        "exp": expire,
        "type": "reset",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        raise ValueError(f"Invalid token: {e}") from e
