import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.merchant import Merchant


class MerchantRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> Merchant:
        merchant = Merchant(**kwargs)
        self.session.add(merchant)
        await self.session.flush()
        return merchant

    async def get_by_id(self, merchant_id: uuid.UUID) -> Merchant | None:
        return await self.session.get(Merchant, merchant_id)

    async def list(self, offset: int = 0, limit: int = 20) -> tuple[list[Merchant], int]:
        count_stmt = select(func.count()).select_from(Merchant)
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = select(Merchant).offset(offset).limit(limit).order_by(Merchant.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def update(self, merchant: Merchant, **kwargs) -> Merchant:
        for key, value in kwargs.items():
            if value is not None:
                setattr(merchant, key, value)
        await self.session.flush()
        await self.session.refresh(merchant)
        return merchant
