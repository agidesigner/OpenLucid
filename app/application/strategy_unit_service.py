import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError
from app.infrastructure.offer_repo import OfferRepository
from app.infrastructure.strategy_unit_repo import StrategyUnitRepository
from app.models.strategy_unit import StrategyUnit
from app.schemas.strategy_unit import StrategyUnitCreate, StrategyUnitUpdate


class StrategyUnitService:
    def __init__(self, session: AsyncSession):
        self.repo = StrategyUnitRepository(session)
        self.offer_repo = OfferRepository(session)

    async def create(self, data: StrategyUnitCreate) -> StrategyUnit:
        offer = await self.offer_repo.get_by_id(data.offer_id)
        if not offer:
            raise NotFoundError("Offer", str(data.offer_id))
        return await self.repo.create(**data.model_dump())

    async def get(self, unit_id: uuid.UUID) -> StrategyUnit:
        unit = await self.repo.get_by_id(unit_id)
        if not unit:
            raise NotFoundError("StrategyUnit", str(unit_id))
        return unit

    async def list(
        self,
        offer_id: uuid.UUID | None = None,
        merchant_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[StrategyUnit], int]:
        offset = (page - 1) * page_size
        return await self.repo.list(offer_id=offer_id, merchant_id=merchant_id, offset=offset, limit=page_size)

    async def update(self, unit_id: uuid.UUID, data: StrategyUnitUpdate) -> StrategyUnit:
        unit = await self.get(unit_id)
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return unit
        return await self.repo.update(unit, **update_data)

    async def delete(self, unit_id: uuid.UUID) -> None:
        unit = await self.get(unit_id)
        await self.repo.delete(unit)
