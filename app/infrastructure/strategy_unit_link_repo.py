import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.strategy_unit_asset_link import StrategyUnitAssetLink
from app.models.strategy_unit_knowledge_link import StrategyUnitKnowledgeLink


class StrategyUnitKnowledgeLinkRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> StrategyUnitKnowledgeLink:
        link = StrategyUnitKnowledgeLink(**kwargs)
        self.session.add(link)
        await self.session.flush()
        return link

    async def get_by_id(self, link_id: uuid.UUID) -> StrategyUnitKnowledgeLink | None:
        return await self.session.get(StrategyUnitKnowledgeLink, link_id)

    async def list_by_strategy_unit(
        self, strategy_unit_id: uuid.UUID, offset: int = 0, limit: int = 20
    ) -> tuple[list[StrategyUnitKnowledgeLink], int]:
        base = select(StrategyUnitKnowledgeLink).where(
            StrategyUnitKnowledgeLink.strategy_unit_id == strategy_unit_id
        )
        count_q = select(func.count()).select_from(StrategyUnitKnowledgeLink).where(
            StrategyUnitKnowledgeLink.strategy_unit_id == strategy_unit_id
        )
        total = (await self.session.execute(count_q)).scalar_one()
        stmt = base.order_by(
            StrategyUnitKnowledgeLink.priority.desc(),
            StrategyUnitKnowledgeLink.created_at.desc(),
        ).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def delete(self, link: StrategyUnitKnowledgeLink) -> None:
        await self.session.delete(link)
        await self.session.flush()


class StrategyUnitAssetLinkRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> StrategyUnitAssetLink:
        link = StrategyUnitAssetLink(**kwargs)
        self.session.add(link)
        await self.session.flush()
        return link

    async def get_by_id(self, link_id: uuid.UUID) -> StrategyUnitAssetLink | None:
        return await self.session.get(StrategyUnitAssetLink, link_id)

    async def list_by_strategy_unit(
        self, strategy_unit_id: uuid.UUID, offset: int = 0, limit: int = 20
    ) -> tuple[list[StrategyUnitAssetLink], int]:
        base = select(StrategyUnitAssetLink).where(
            StrategyUnitAssetLink.strategy_unit_id == strategy_unit_id
        )
        count_q = select(func.count()).select_from(StrategyUnitAssetLink).where(
            StrategyUnitAssetLink.strategy_unit_id == strategy_unit_id
        )
        total = (await self.session.execute(count_q)).scalar_one()
        stmt = base.order_by(
            StrategyUnitAssetLink.priority.desc(),
            StrategyUnitAssetLink.created_at.desc(),
        ).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def delete(self, link: StrategyUnitAssetLink) -> None:
        await self.session.delete(link)
        await self.session.flush()
