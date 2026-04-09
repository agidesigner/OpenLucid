from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError, NotFoundError
from app.infrastructure.creation_repo import CreationRepository
from app.models.creation import Creation
from app.models.merchant import Merchant
from app.models.offer import Offer
from app.schemas.creation import CreationCreate, CreationUpdate

logger = logging.getLogger(__name__)


class CreationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = CreationRepository(session)

    async def create(self, data: CreationCreate) -> Creation:
        # Resolve merchant_id: prefer offer_id derivation, then explicit, then fallback
        merchant_id = data.merchant_id
        offer_id = data.offer_id

        if offer_id and not merchant_id:
            offer = await self.session.get(Offer, offer_id)
            if not offer:
                raise NotFoundError("Offer", str(offer_id))
            merchant_id = offer.merchant_id

        if not merchant_id:
            # Single-merchant fallback (most self-hosted users have one merchant)
            result = await self.session.execute(
                select(Merchant.id).order_by(Merchant.created_at).limit(2)
            )
            ids = [row[0] for row in result]
            if len(ids) == 1:
                merchant_id = ids[0]
            elif len(ids) > 1:
                raise AppError(
                    code="MERCHANT_REQUIRED",
                    message="merchant_id is required when multiple merchants exist; or pass an offer_id and it will be derived from there",
                    status_code=400,
                )
            else:
                raise AppError(
                    code="NO_MERCHANT",
                    message="No merchant found — create one first",
                    status_code=400,
                )

        creation = await self.repo.create(
            merchant_id=merchant_id,
            offer_id=offer_id,
            title=data.title.strip(),
            content=data.content,
            content_type=data.content_type or "general",
            tags=data.tags or None,
            source_app=data.source_app or "manual",
            source_note=data.source_note,
        )
        logger.info(
            "Creation saved: id=%s title=%r source=%s offer=%s",
            creation.id, creation.title[:60], creation.source_app, offer_id,
        )
        return creation

    async def get(self, creation_id: uuid.UUID) -> Creation:
        creation = await self.repo.get_by_id(creation_id)
        if not creation:
            raise NotFoundError("Creation", str(creation_id))
        return creation

    async def list(
        self,
        merchant_id: uuid.UUID | None = None,
        offer_id: uuid.UUID | None = None,
        content_type: str | None = None,
        source_app: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Creation], int]:
        offset = (page - 1) * page_size
        return await self.repo.list(
            merchant_id=merchant_id,
            offer_id=offer_id,
            content_type=content_type,
            source_app=source_app,
            q=q,
            offset=offset,
            limit=page_size,
        )

    async def update(self, creation_id: uuid.UUID, data: CreationUpdate) -> Creation:
        creation = await self.repo.update(
            creation_id,
            title=data.title.strip() if data.title else None,
            content=data.content,
            content_type=data.content_type,
            tags=data.tags,
            source_note=data.source_note,
        )
        if not creation:
            raise NotFoundError("Creation", str(creation_id))
        return creation

    async def delete(self, creation_id: uuid.UUID) -> None:
        ok = await self.repo.delete(creation_id)
        if not ok:
            raise NotFoundError("Creation", str(creation_id))
