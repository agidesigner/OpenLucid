from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.creation import Creation


class CreationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> Creation:
        creation = Creation(**kwargs)
        self.session.add(creation)
        await self.session.flush()
        return creation

    async def get_by_id(self, creation_id: uuid.UUID) -> Creation | None:
        return await self.session.get(Creation, creation_id)

    async def list(
        self,
        merchant_id: uuid.UUID | None = None,
        offer_id: uuid.UUID | None = None,
        content_type: str | None = None,
        source_app: str | None = None,
        q: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Creation], int]:
        base = select(Creation)
        count_base = select(func.count()).select_from(Creation)

        if merchant_id:
            base = base.where(Creation.merchant_id == merchant_id)
            count_base = count_base.where(Creation.merchant_id == merchant_id)
        if offer_id:
            base = base.where(Creation.offer_id == offer_id)
            count_base = count_base.where(Creation.offer_id == offer_id)
        if content_type:
            base = base.where(Creation.content_type == content_type)
            count_base = count_base.where(Creation.content_type == content_type)
        if source_app:
            # Trailing ":*" turns the value into a prefix glob — used by
            # the creations.html "MCP External" filter, which needs to
            # cover any mcp:<client> value (mcp:external, mcp:claude-code,
            # mcp:cursor, …). Plain values still match exactly so the
            # existing per-app filter (?source_app=script_writer) keeps
            # working.
            if source_app.endswith(":*"):
                clause = Creation.source_app.like(source_app[:-1] + "%")
            else:
                clause = Creation.source_app == source_app
            base = base.where(clause)
            count_base = count_base.where(clause)
        if q:
            pattern = f"%{q}%"
            search_clause = or_(
                Creation.title.ilike(pattern),
                Creation.content.ilike(pattern),
            )
            base = base.where(search_clause)
            count_base = count_base.where(search_clause)

        total = (await self.session.execute(count_base)).scalar_one()
        stmt = base.offset(offset).limit(limit).order_by(Creation.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def update(self, creation_id: uuid.UUID, **kwargs) -> Creation | None:
        creation = await self.get_by_id(creation_id)
        if not creation:
            return None
        for key, value in kwargs.items():
            if value is not None and hasattr(creation, key):
                setattr(creation, key, value)
        await self.session.flush()
        return creation

    async def delete(self, creation_id: uuid.UUID) -> bool:
        creation = await self.get_by_id(creation_id)
        if not creation:
            return False
        await self.session.delete(creation)
        await self.session.flush()
        return True
