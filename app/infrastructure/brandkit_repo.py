from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.brandkit import BrandKit
from app.models.brandkit_asset_link import BrandKitAssetLink


class BrandKitRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> BrandKit:
        kit = BrandKit(**kwargs)
        self.session.add(kit)
        await self.session.flush()
        return kit

    async def get_by_id(self, kit_id: uuid.UUID) -> BrandKit | None:
        return await self.session.get(BrandKit, kit_id)

    async def list(
        self,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
        merchant_id: uuid.UUID | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[BrandKit], int]:
        base = select(BrandKit)
        count_base = select(func.count()).select_from(BrandKit)

        if scope_type:
            base = base.where(BrandKit.scope_type == scope_type)
            count_base = count_base.where(BrandKit.scope_type == scope_type)
        if scope_id:
            base = base.where(BrandKit.scope_id == scope_id)
            count_base = count_base.where(BrandKit.scope_id == scope_id)
        if merchant_id:
            # merchant_id filter: return merchant-level kit + all offer-level kits for that merchant
            # For simplicity, filter by scope_id matching merchant_id when scope_type is merchant
            # The service layer handles the full merchant listing logic
            base = base.where(BrandKit.scope_id == merchant_id, BrandKit.scope_type == "merchant")
            count_base = count_base.where(BrandKit.scope_id == merchant_id, BrandKit.scope_type == "merchant")

        total = (await self.session.execute(count_base)).scalar_one()
        stmt = base.offset(offset).limit(limit).order_by(BrandKit.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_by_scope(self, scope_type: str, scope_id: uuid.UUID) -> BrandKit | None:
        stmt = select(BrandKit).where(
            BrandKit.scope_type == scope_type,
            BrandKit.scope_id == scope_id,
        ).limit(1)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_by_merchant_all(
        self, merchant_id: uuid.UUID, offer_ids: list[uuid.UUID] | None = None
    ) -> list[BrandKit]:
        """Return merchant kit + all offer kits belonging to that merchant."""
        from sqlalchemy import or_

        conditions = [
            (BrandKit.scope_type == "merchant") & (BrandKit.scope_id == merchant_id),
        ]
        if offer_ids:
            conditions.append(
                (BrandKit.scope_type == "offer") & (BrandKit.scope_id.in_(offer_ids))
            )
        stmt = select(BrandKit).where(or_(*conditions)).order_by(BrandKit.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update(self, kit: BrandKit, **kwargs) -> BrandKit:
        for key, value in kwargs.items():
            setattr(kit, key, value)
        await self.session.flush()
        await self.session.refresh(kit)
        return kit

    async def delete(self, kit: BrandKit) -> None:
        await self.session.delete(kit)
        await self.session.flush()


class BrandKitAssetLinkRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> BrandKitAssetLink:
        link = BrandKitAssetLink(**kwargs)
        self.session.add(link)
        await self.session.flush()
        await self.session.refresh(link, ["asset"])
        return link

    async def get_by_id(self, link_id: uuid.UUID) -> BrandKitAssetLink | None:
        return await self.session.get(BrandKitAssetLink, link_id)

    async def list_by_brandkit(
        self, brandkit_id: uuid.UUID, offset: int = 0, limit: int = 20
    ) -> tuple[list[BrandKitAssetLink], int]:
        base = select(BrandKitAssetLink).where(
            BrandKitAssetLink.brandkit_id == brandkit_id
        )
        count_q = select(func.count()).select_from(BrandKitAssetLink).where(
            BrandKitAssetLink.brandkit_id == brandkit_id
        )
        total = (await self.session.execute(count_q)).scalar_one()
        stmt = base.order_by(
            BrandKitAssetLink.priority.desc(),
            BrandKitAssetLink.created_at.desc(),
        ).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def delete(self, link: BrandKitAssetLink) -> None:
        await self.session.delete(link)
        await self.session.flush()
