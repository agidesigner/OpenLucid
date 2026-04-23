from sqlalchemy import Column, String

from app.models.base import BaseModel


class GuestAccess(BaseModel):
    __tablename__ = "guest_access"

    token_hash = Column(String(64), nullable=False, unique=True, index=True)
