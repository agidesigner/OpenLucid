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
        # Set every passed kwarg, INCLUDING None. Filtering None here was
        # a defensive over-reach: it broke "PATCH a field to null to
        # clear it", which is the canonical REST semantic. Pre-v1.1.5
        # an attempt to wipe ``description`` / ``core_selling_points_json``
        # / etc returned 200 with no DB change — silent no-op.
        # The "did the caller mean to send this field at all?" question
        # is already answered upstream by ``OfferUpdate.model_dump
        # (exclude_unset=True)``: only fields the caller explicitly set
        # arrive in kwargs. So if it's here, write it — even if it's None.
        for key, value in kwargs.items():
            setattr(offer, key, value)
        await self.session.flush()
        await self.session.refresh(offer)
        return offer
