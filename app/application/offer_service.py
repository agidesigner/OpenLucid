import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError
from app.infrastructure.merchant_repo import MerchantRepository
from app.infrastructure.offer_repo import OfferRepository
from app.models.creation import Creation
from app.models.offer import Offer
from app.schemas.offer import OfferCreate, OfferUpdate

logger = logging.getLogger(__name__)


class OfferService:
    def __init__(self, session: AsyncSession):
        self.repo = OfferRepository(session)
        self.merchant_repo = MerchantRepository(session)
        self.session = session

    async def create(self, data: OfferCreate) -> Offer:
        merchant = await self.merchant_repo.get_by_id(data.merchant_id)
        if not merchant:
            raise NotFoundError("Merchant", str(data.merchant_id))
        offer = await self.repo.create(**data.model_dump())
        await self._infer_and_set_offer_model(offer)
        return offer

    async def _infer_and_set_offer_model(self, offer: Offer) -> None:
        try:
            from app.adapters.ai import get_ai_adapter
            ai = await get_ai_adapter(self.session, scene_key="offer_model")
            model = await ai.infer_offer_model(
                name=offer.name,
                description=offer.description or "",
                offer_type=offer.offer_type,
            )
            await self.repo.update(offer, offer_model=model)
            logger.info("Inferred offer_model='%s' for offer '%s'", model, offer.name)
        except Exception:
            logger.warning("Failed to infer offer_model for offer '%s'", offer.name, exc_info=True)

    async def get(self, offer_id: uuid.UUID) -> Offer:
        offer = await self.repo.get_by_id(offer_id)
        if not offer:
            raise NotFoundError("Offer", str(offer_id))
        return offer

    async def list(
        self, merchant_id: uuid.UUID | None = None, page: int = 1, page_size: int = 20
    ) -> tuple[list[Offer], int]:
        offset = (page - 1) * page_size
        return await self.repo.list(merchant_id=merchant_id, offset=offset, limit=page_size)

    async def delete(self, offer_id: uuid.UUID) -> None:
        offer = await self.get(offer_id)
        await self.repo.delete(offer)

    async def update(self, offer_id: uuid.UUID, data: OfferUpdate) -> Offer:
        offer = await self.get(offer_id)
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return offer
        return await self.repo.update(offer, **update_data)

    async def get_consumption_summary(self, offer_id: uuid.UUID) -> dict:
        """How has this offer's knowledge been consumed?

        Aggregates Creation rows linked to the offer — total count, breakdown
        by source_app, and last-used timestamp. Feeds the Offer page's
        "Consumption" card so users can see whether agents / apps are using
        the world they've built.
        """
        await self.get(offer_id)  # 404 if offer missing

        stmt = (
            select(Creation.source_app, func.count(Creation.id), func.max(Creation.created_at))
            .where(Creation.offer_id == offer_id)
            .group_by(Creation.source_app)
        )
        rows = (await self.session.execute(stmt)).all()

        by_source: dict[str, int] = {}
        total = 0
        last_used_at = None
        for source_app, count, max_created in rows:
            by_source[source_app or "unknown"] = int(count)
            total += int(count)
            if max_created is not None and (last_used_at is None or max_created > last_used_at):
                last_used_at = max_created

        return {
            "creations_total": total,
            "by_source": by_source,
            "last_used_at": last_used_at.isoformat() if last_used_at else None,
        }
