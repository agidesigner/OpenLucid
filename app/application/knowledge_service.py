from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError
from app.infrastructure.knowledge_repo import KnowledgeItemRepository
from app.models.knowledge_item import KnowledgeItem
from app.schemas.knowledge import KnowledgeItemCreate, KnowledgeItemUpdate


class KnowledgeService:
    def __init__(self, session: AsyncSession):
        self.repo = KnowledgeItemRepository(session)

    async def create(self, data: KnowledgeItemCreate) -> KnowledgeItem:
        return await self.repo.create(**data.model_dump())

    async def batch_create(self, items: list[KnowledgeItemCreate]) -> list[KnowledgeItem]:
        # Single ``session.add_all`` + one ``flush`` lets SQLAlchemy batch the
        # writes into a single multi-row INSERT (insertmanyvalues), instead
        # of N sequential round-trips. Wizard-generated KB batches are
        # typically 10–25 items, so this is the dominant path.
        models = [KnowledgeItem(**data.model_dump()) for data in items]
        self.repo.session.add_all(models)
        await self.repo.session.flush()
        return models

    async def batch_upsert(self, items: list[KnowledgeItemCreate]) -> dict:
        updated, created, results = 0, 0, []
        for data in items:
            existing = await self.repo.find_by_title(
                data.scope_type, data.scope_id, data.knowledge_type, data.title
            )
            if existing:
                item = await self.repo.update(existing, content_raw=data.content_raw)
                updated += 1
            else:
                item = await self.repo.create(**data.model_dump())
                created += 1
            results.append(item)
        return {"updated": updated, "created": created, "items": results}

    async def get(self, item_id: uuid.UUID) -> KnowledgeItem:
        item = await self.repo.get_by_id(item_id)
        if not item:
            raise NotFoundError("KnowledgeItem", str(item_id))
        return item

    async def list(
        self,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
        knowledge_type: list[str] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[KnowledgeItem], int]:
        offset = (page - 1) * page_size
        return await self.repo.list(
            scope_type=scope_type,
            scope_id=scope_id,
            knowledge_type=knowledge_type,
            offset=offset,
            limit=page_size,
        )

    async def update(self, item_id: uuid.UUID, data: KnowledgeItemUpdate) -> KnowledgeItem:
        item = await self.get(item_id)
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return item
        return await self.repo.update(item, **update_data)

    async def delete(self, item_id: uuid.UUID) -> None:
        deleted = await self.repo.delete(item_id)
        if not deleted:
            raise NotFoundError("KnowledgeItem", str(item_id))
