from sqlalchemy import Column, String

from app.models.base import BaseModel


class GuestAccess(BaseModel):
    __tablename__ = "guest_access"

    # SHA-256 of raw_token. Primary lookup key for verify() — keeps the
    # fast path for the middleware and keeps a defence-in-depth layer
    # even if the raw column is stolen in a backup without the DB key.
    token_hash = Column(String(64), nullable=False, unique=True, index=True)

    # Raw share token. Stored so the owner can view + copy the URL in
    # Settings → 访客分享 anytime, not just once at creation. Nullable
    # because rows created before this column existed have no raw value;
    # the UI handles that with a "regenerate to reveal" fallback.
    raw_token = Column(String(128), nullable=True)
