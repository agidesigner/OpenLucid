import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError
from app.infrastructure.merchant_repo import MerchantRepository
from app.infrastructure.offer_repo import OfferRepository
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
