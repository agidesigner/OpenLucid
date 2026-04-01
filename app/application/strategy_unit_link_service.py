import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, NotFoundError
from app.infrastructure.asset_repo import AssetRepository
from app.infrastructure.knowledge_repo import KnowledgeItemRepository
from app.infrastructure.strategy_unit_link_repo import (
    StrategyUnitAssetLinkRepository,
    StrategyUnitKnowledgeLinkRepository,
)
from app.infrastructure.strategy_unit_repo import StrategyUnitRepository
from app.models.strategy_unit_asset_link import StrategyUnitAssetLink
from app.models.strategy_unit_knowledge_link import StrategyUnitKnowledgeLink
from app.schemas.strategy_unit_link import AssetLinkCreate, KnowledgeLinkCreate


class StrategyUnitKnowledgeLinkService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = StrategyUnitKnowledgeLinkRepository(session)
        self.su_repo = StrategyUnitRepository(session)
        self.ki_repo = KnowledgeItemRepository(session)

    async def create(
        self, strategy_unit_id: uuid.UUID, data: KnowledgeLinkCreate
    ) -> StrategyUnitKnowledgeLink:
        su = await self.su_repo.get_by_id(strategy_unit_id)
        if not su:
            raise NotFoundError("StrategyUnit", str(strategy_unit_id))
        ki = await self.ki_repo.get_by_id(data.knowledge_item_id)
        if not ki:
            raise NotFoundError("KnowledgeItem", str(data.knowledge_item_id))
        try:
            link = await self.repo.create(
                strategy_unit_id=strategy_unit_id,
                knowledge_item_id=data.knowledge_item_id,
                role=data.role.value,
                priority=data.priority,
                note=data.note,
            )
            return link
        except IntegrityError:
            await self.session.rollback()
            raise ConflictError(
                f"Knowledge item '{data.knowledge_item_id}' is already linked to strategy unit '{strategy_unit_id}'"
            )

    async def list(
        self, strategy_unit_id: uuid.UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[StrategyUnitKnowledgeLink], int]:
        offset = (page - 1) * page_size
        return await self.repo.list_by_strategy_unit(strategy_unit_id, offset=offset, limit=page_size)

    async def delete(self, strategy_unit_id: uuid.UUID, link_id: uuid.UUID) -> None:
        link = await self.repo.get_by_id(link_id)
        if not link or link.strategy_unit_id != strategy_unit_id:
            raise NotFoundError("StrategyUnitKnowledgeLink", str(link_id))
        await self.repo.delete(link)


class StrategyUnitAssetLinkService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = StrategyUnitAssetLinkRepository(session)
        self.su_repo = StrategyUnitRepository(session)
        self.asset_repo = AssetRepository(session)

    async def create(
        self, strategy_unit_id: uuid.UUID, data: AssetLinkCreate
    ) -> StrategyUnitAssetLink:
        su = await self.su_repo.get_by_id(strategy_unit_id)
        if not su:
            raise NotFoundError("StrategyUnit", str(strategy_unit_id))
        asset = await self.asset_repo.get_by_id(data.asset_id)
        if not asset:
            raise NotFoundError("Asset", str(data.asset_id))
        try:
            link = await self.repo.create(
                strategy_unit_id=strategy_unit_id,
                asset_id=data.asset_id,
                role=data.role.value,
                priority=data.priority,
                note=data.note,
            )
            return link
        except IntegrityError:
            await self.session.rollback()
            raise ConflictError(
                f"Asset '{data.asset_id}' is already linked to strategy unit '{strategy_unit_id}'"
            )

    async def list(
        self, strategy_unit_id: uuid.UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[StrategyUnitAssetLink], int]:
        offset = (page - 1) * page_size
        return await self.repo.list_by_strategy_unit(strategy_unit_id, offset=offset, limit=page_size)

    async def delete(self, strategy_unit_id: uuid.UUID, link_id: uuid.UUID) -> None:
        link = await self.repo.get_by_id(link_id)
        if not link or link.strategy_unit_id != strategy_unit_id:
            raise NotFoundError("StrategyUnitAssetLink", str(link_id))
        await self.repo.delete(link)
