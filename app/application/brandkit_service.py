import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, NotFoundError
from app.infrastructure.asset_repo import AssetRepository
from app.infrastructure.brandkit_repo import BrandKitAssetLinkRepository, BrandKitRepository
from app.infrastructure.merchant_repo import MerchantRepository
from app.infrastructure.offer_repo import OfferRepository
from app.models.brandkit import BrandKit, BrandKitColor, BrandKitFont
from app.models.brandkit_asset_link import BrandKitAssetLink
from app.schemas.brandkit import (
    BrandKitAssetLinkCreate,
    BrandKitColorCreate,
    BrandKitColorUpdate,
    BrandKitCreate,
    BrandKitFontCreate,
    BrandKitFontUpdate,
    BrandKitUpdate,
)


class BrandKitService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = BrandKitRepository(session)
        self.offer_repo = OfferRepository(session)
        self.merchant_repo = MerchantRepository(session)

    async def create(self, data: BrandKitCreate) -> BrandKit:
        if data.scope_type.value == "merchant":
            existing = await self.repo.get_by_scope("merchant", data.scope_id)
            if existing:
                raise ConflictError("This merchant already has a brand kit. Only one company-level brand kit is allowed per merchant")
        payload = data.model_dump()
        if not payload.get("name"):
            payload["name"] = await self._derive_name(data.scope_type.value, data.scope_id)
        return await self.repo.create(**payload)

    async def _derive_name(self, scope_type: str, scope_id: uuid.UUID) -> str | None:
        if scope_type == "merchant":
            merchant = await self.merchant_repo.get_by_id(scope_id)
            return merchant.name if merchant else None
        if scope_type == "offer":
            offer = await self.offer_repo.get_by_id(scope_id)
            return offer.name if offer else None
        return None

    async def get(self, kit_id: uuid.UUID) -> BrandKit:
        kit = await self.repo.get_by_id(kit_id)
        if not kit:
            raise NotFoundError("BrandKit", str(kit_id))
        return kit

    async def get_merged(self, kit_id: uuid.UUID) -> BrandKit:
        """Offer-level kit inherits brand_voice from the merchant kit when its own is empty.

        Previously this merged seven JSONB fields; the v2 schema only has
        ``brand_voice`` as structured text. Colors / fonts / assets are owned
        rows and don't need per-field inheritance — the UI can fall back to
        the merchant kit's rows directly when an offer kit has none.
        """
        kit = await self.get(kit_id)
        if kit.scope_type == "offer" and not kit.brand_voice:
            offer = await self.offer_repo.get_by_id(kit.scope_id)
            if offer:
                merchant_kit = await self.repo.get_by_scope("merchant", offer.merchant_id)
                if merchant_kit and merchant_kit.brand_voice:
                    kit.brand_voice = merchant_kit.brand_voice
        return kit

    async def list(
        self,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
        merchant_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[BrandKit], int]:
        offset = (page - 1) * page_size
        return await self.repo.list(
            scope_type=scope_type,
            scope_id=scope_id,
            merchant_id=merchant_id,
            offset=offset,
            limit=page_size,
        )

    async def list_for_merchant(self, merchant_id: uuid.UUID) -> dict:
        offers, _ = await self.offer_repo.list(merchant_id=merchant_id, offset=0, limit=200)
        offer_ids = [o.id for o in offers]
        all_kits = await self.repo.list_by_merchant_all(merchant_id, offer_ids)

        merchant_kit = None
        offer_kits = []
        for kit in all_kits:
            if kit.scope_type == "merchant":
                merchant_kit = kit
            else:
                offer_kits.append(kit)

        return {
            "merchant_kit": merchant_kit,
            "offer_kits": offer_kits,
            "offers": {str(o.id): o.name for o in offers},
        }

    async def update(self, kit_id: uuid.UUID, data: BrandKitUpdate) -> BrandKit:
        kit = await self.get(kit_id)
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return kit
        return await self.repo.update(kit, **update_data)

    async def delete(self, kit_id: uuid.UUID) -> None:
        kit = await self.get(kit_id)
        await self.repo.delete(kit)


class BrandKitLinkService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = BrandKitAssetLinkRepository(session)
        self.kit_repo = BrandKitRepository(session)
        self.asset_repo = AssetRepository(session)

    async def create(
        self, brandkit_id: uuid.UUID, data: BrandKitAssetLinkCreate
    ) -> BrandKitAssetLink:
        kit = await self.kit_repo.get_by_id(brandkit_id)
        if not kit:
            raise NotFoundError("BrandKit", str(brandkit_id))
        asset = await self.asset_repo.get_by_id(data.asset_id)
        if not asset:
            raise NotFoundError("Asset", str(data.asset_id))
        try:
            link = await self.repo.create(
                brandkit_id=brandkit_id,
                asset_id=data.asset_id,
                role=data.role.value,
                priority=data.priority,
                note=data.note,
            )
            return link
        except IntegrityError:
            await self.session.rollback()
            raise ConflictError(
                f"Asset '{data.asset_id}' is already linked to brandkit '{brandkit_id}'"
            )

    async def list(
        self, brandkit_id: uuid.UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[BrandKitAssetLink], int]:
        offset = (page - 1) * page_size
        return await self.repo.list_by_brandkit(brandkit_id, offset=offset, limit=page_size)

    async def delete(self, brandkit_id: uuid.UUID, link_id: uuid.UUID) -> None:
        link = await self.repo.get_by_id(link_id)
        if not link or link.brandkit_id != brandkit_id:
            raise NotFoundError("BrandKitAssetLink", str(link_id))
        await self.repo.delete(link)

    async def set_primary(self, brandkit_id: uuid.UUID, link_id: uuid.UUID) -> BrandKitAssetLink:
        """Mark this link as primary within its role (priority=0) and demote
        any other link with the same ``role`` to priority>=1. Used for the
        HeyGen-style "one primary logo, several alternates" pattern.
        """
        from sqlalchemy import select
        target = await self.repo.get_by_id(link_id)
        if not target or target.brandkit_id != brandkit_id:
            raise NotFoundError("BrandKitAssetLink", str(link_id))
        stmt = select(BrandKitAssetLink).where(
            BrandKitAssetLink.brandkit_id == brandkit_id,
            BrandKitAssetLink.role == target.role,
            BrandKitAssetLink.id != target.id,
        )
        others = (await self.session.execute(stmt)).scalars().all()
        for i, o in enumerate(others, start=1):
            o.priority = i
        target.priority = 0
        await self.session.commit()
        await self.session.refresh(target)
        return target


# ── Colors & Fonts: simple CRUD, no inheritance ──────────────


class BrandKitColorService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.kit_repo = BrandKitRepository(session)

    async def list(self, brandkit_id: uuid.UUID) -> list[BrandKitColor]:
        from sqlalchemy import select
        stmt = (
            select(BrandKitColor)
            .where(BrandKitColor.brandkit_id == brandkit_id)
            .order_by(BrandKitColor.priority, BrandKitColor.created_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create(self, brandkit_id: uuid.UUID, data: BrandKitColorCreate) -> BrandKitColor:
        kit = await self.kit_repo.get_by_id(brandkit_id)
        if not kit:
            raise NotFoundError("BrandKit", str(brandkit_id))
        color = BrandKitColor(
            brandkit_id=brandkit_id,
            role=data.role.value,
            hex=data.hex,
            priority=data.priority,
        )
        self.session.add(color)
        await self.session.commit()
        await self.session.refresh(color)
        return color

    async def update(
        self, brandkit_id: uuid.UUID, color_id: uuid.UUID, data: BrandKitColorUpdate
    ) -> BrandKitColor:
        color = await self.session.get(BrandKitColor, color_id)
        if not color or color.brandkit_id != brandkit_id:
            raise NotFoundError("BrandKitColor", str(color_id))
        update = data.model_dump(exclude_unset=True)
        if "role" in update and update["role"] is not None:
            color.role = update["role"].value
        if "hex" in update and update["hex"] is not None:
            color.hex = update["hex"]
        if "priority" in update and update["priority"] is not None:
            color.priority = update["priority"]
        await self.session.commit()
        await self.session.refresh(color)
        return color

    async def delete(self, brandkit_id: uuid.UUID, color_id: uuid.UUID) -> None:
        color = await self.session.get(BrandKitColor, color_id)
        if not color or color.brandkit_id != brandkit_id:
            raise NotFoundError("BrandKitColor", str(color_id))
        await self.session.delete(color)
        await self.session.commit()


class BrandKitFontService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.kit_repo = BrandKitRepository(session)

    async def list(self, brandkit_id: uuid.UUID) -> list[BrandKitFont]:
        from sqlalchemy import select
        stmt = (
            select(BrandKitFont)
            .where(BrandKitFont.brandkit_id == brandkit_id)
            .order_by(BrandKitFont.priority, BrandKitFont.created_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create(self, brandkit_id: uuid.UUID, data: BrandKitFontCreate) -> BrandKitFont:
        kit = await self.kit_repo.get_by_id(brandkit_id)
        if not kit:
            raise NotFoundError("BrandKit", str(brandkit_id))
        font = BrandKitFont(
            brandkit_id=brandkit_id,
            role=data.role.value,
            font_name=data.font_name,
            font_url=data.font_url,
            priority=data.priority,
        )
        self.session.add(font)
        await self.session.commit()
        await self.session.refresh(font)
        return font

    async def update(
        self, brandkit_id: uuid.UUID, font_id: uuid.UUID, data: BrandKitFontUpdate
    ) -> BrandKitFont:
        font = await self.session.get(BrandKitFont, font_id)
        if not font or font.brandkit_id != brandkit_id:
            raise NotFoundError("BrandKitFont", str(font_id))
        update = data.model_dump(exclude_unset=True)
        if "role" in update and update["role"] is not None:
            font.role = update["role"].value
        if "font_name" in update and update["font_name"] is not None:
            font.font_name = update["font_name"]
        if "font_url" in update:
            font.font_url = update["font_url"]
        if "priority" in update and update["priority"] is not None:
            font.priority = update["priority"]
        await self.session.commit()
        await self.session.refresh(font)
        return font

    async def delete(self, brandkit_id: uuid.UUID, font_id: uuid.UUID) -> None:
        font = await self.session.get(BrandKitFont, font_id)
        if not font or font.brandkit_id != brandkit_id:
            raise NotFoundError("BrandKitFont", str(font_id))
        await self.session.delete(font)
        await self.session.commit()
