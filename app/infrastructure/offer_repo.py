import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.offer import Offer


class OfferRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> Offer:
        offer = Offer(**kwargs)
        self.session.add(offer)
        await self.session.flush()
        return offer

    async def get_by_id(self, offer_id: uuid.UUID) -> Offer | None:
        return await self.session.get(Offer, offer_id)

    async def list(
        self, merchant_id: uuid.UUID | None = None, offset: int = 0, limit: int = 20
    ) -> tuple[list[Offer], int]:
        base = select(Offer)
        count_base = select(func.count()).select_from(Offer)

        if merchant_id:
            base = base.where(Offer.merchant_id == merchant_id)
            count_base = count_base.where(Offer.merchant_id == merchant_id)

        total = (await self.session.execute(count_base)).scalar_one()
        stmt = base.offset(offset).limit(limit).order_by(Offer.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def delete(self, offer: Offer) -> None:
        await self.session.delete(offer)
        await self.session.flush()

    async def update(self, offer: Offer, **kwargs) -> Offer:
        for key, value in kwargs.items():
            if value is not None:
                setattr(offer, key, value)
        await self.session.flush()
        await self.session.refresh(offer)
        return offer
