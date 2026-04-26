import uuid

from sqlalchemy import delete, select, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
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

    async def upsert_many_full(
        self, items: list[dict]
    ) -> tuple[int, int, list[uuid.UUID]]:
        """Bulk upsert via the ``uq_knowledge_title`` unique constraint
        (``scope_type, scope_id, knowledge_type, title``). Designed for the
        AI-inference persist path: when a row with the same title already
        exists, the new ``content_raw / source_type / source_ref / language``
        overwrite it; ``confidence`` only overwrites when the new payload
        actually carries one (``COALESCE(EXCLUDED.confidence, current)``)
        so a re-run that omits confidence doesn't wipe a prior score.

        Returns ``(created, updated, ids)``. The created/updated split comes
        from PostgreSQL's ``xmax = 0`` trick on ``RETURNING`` — newly-inserted
        rows have ``xmax = 0``, conflict-updated rows have a non-zero ``xmax``.

        Each item dict MUST include: ``scope_type, scope_id, knowledge_type,
        title, content_raw, source_type, source_ref, language``. Optional:
        ``confidence`` (float | None).
        """
        if not items:
            return 0, 0, []
        stmt = pg_insert(KnowledgeItem).values(items)
        stmt = stmt.on_conflict_do_update(
            index_elements=["scope_type", "scope_id", "knowledge_type", "title"],
            set_={
                "content_raw": stmt.excluded.content_raw,
                "source_type": stmt.excluded.source_type,
                "source_ref": stmt.excluded.source_ref,
                "language": stmt.excluded.language,
                "confidence": func.coalesce(
                    stmt.excluded.confidence, KnowledgeItem.confidence
                ),
                "updated_at": func.now(),
            },
        ).returning(
            KnowledgeItem.id,
            text("(xmax = 0) AS inserted"),
        )
        result = await self.session.execute(stmt)
        rows = result.all()
        ids = [r[0] for r in rows]
        created = sum(1 for r in rows if r[1])
        updated = len(rows) - created
        await self.session.flush()
        return created, updated, ids

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
