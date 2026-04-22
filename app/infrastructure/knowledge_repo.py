import uuid

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_item import KnowledgeItem


class KnowledgeItemRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> KnowledgeItem:
        item = KnowledgeItem(**kwargs)
        self.session.add(item)
        await self.session.flush()
        return item

    async def get_by_id(self, item_id: uuid.UUID) -> KnowledgeItem | None:
        return await self.session.get(KnowledgeItem, item_id)

    async def list(
        self,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
        knowledge_type: list[str] | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[KnowledgeItem], int]:
        base = select(KnowledgeItem)
        count_base = select(func.count()).select_from(KnowledgeItem)

        if scope_type:
            base = base.where(KnowledgeItem.scope_type == scope_type)
            count_base = count_base.where(KnowledgeItem.scope_type == scope_type)
        if scope_id:
            base = base.where(KnowledgeItem.scope_id == scope_id)
            count_base = count_base.where(KnowledgeItem.scope_id == scope_id)
        if knowledge_type:
            base = base.where(KnowledgeItem.knowledge_type.in_(knowledge_type))
            count_base = count_base.where(KnowledgeItem.knowledge_type.in_(knowledge_type))

        total = (await self.session.execute(count_base)).scalar_one()
        stmt = base.offset(offset).limit(limit).order_by(KnowledgeItem.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def update(self, item: KnowledgeItem, **kwargs) -> KnowledgeItem:
        for key, value in kwargs.items():
            if value is not None:
                setattr(item, key, value)
        await self.session.flush()
        await self.session.refresh(item)
        return item

    async def find_by_title(
        self, scope_type: str, scope_id: uuid.UUID, knowledge_type: str, title: str
    ) -> KnowledgeItem | None:
        stmt = select(KnowledgeItem).where(
            KnowledgeItem.scope_type == scope_type,
            KnowledgeItem.scope_id == scope_id,
            KnowledgeItem.knowledge_type == knowledge_type,
            KnowledgeItem.title == title,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete(self, item_id: uuid.UUID) -> bool:
        stmt = delete(KnowledgeItem).where(KnowledgeItem.id == item_id)
        result = await self.session.execute(stmt)
        return result.rowcount > 0
