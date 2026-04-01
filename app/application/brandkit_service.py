import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError, ConflictError, NotFoundError
from app.infrastructure.asset_repo import AssetRepository
from app.infrastructure.brandkit_repo import BrandKitAssetLinkRepository, BrandKitRepository
from app.infrastructure.offer_repo import OfferRepository
from app.models.brandkit import BrandKit
from app.models.brandkit_asset_link import BrandKitAssetLink
from app.schemas.brandkit import BrandKitCreate, BrandKitResponse, BrandKitUpdate, BrandKitAssetLinkCreate

PROFILE_FIELDS = [
    "style_profile_json",
    "product_visual_profile_json",
    "service_scene_profile_json",
    "persona_profile_json",
    "visual_do_json",
    "visual_dont_json",
    "reference_prompt_json",
]


class BrandKitService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = BrandKitRepository(session)
        self.offer_repo = OfferRepository(session)

    async def create(self, data: BrandKitCreate) -> BrandKit:
        if data.scope_type.value == "merchant":
            existing = await self.repo.get_by_scope("merchant", data.scope_id)
            if existing:
                raise ConflictError("This merchant already has a brand kit. Only one company-level brand kit is allowed per merchant")
        return await self.repo.create(**data.model_dump())

    async def get(self, kit_id: uuid.UUID) -> BrandKit:
        kit = await self.repo.get_by_id(kit_id)
        if not kit:
            raise NotFoundError("BrandKit", str(kit_id))
        return kit

    async def get_merged(self, kit_id: uuid.UUID) -> BrandKitResponse:
        kit = await self.get(kit_id)
        inherited_fields: list[str] = []
        overridden_fields: list[str] = []

        if kit.scope_type == "offer":
            offer = await self.offer_repo.get_by_id(kit.scope_id)
            if offer:
                merchant_kit = await self.repo.get_by_scope("merchant", offer.merchant_id)
                if merchant_kit:
                    for field in PROFILE_FIELDS:
                        offer_val = getattr(kit, field)
                        if offer_val:
                            overridden_fields.append(field)
                        else:
                            merchant_val = getattr(merchant_kit, field)
                            if merchant_val:
                                setattr(kit, field, merchant_val)
                                inherited_fields.append(field)

        resp = BrandKitResponse.model_validate(kit)
        resp.inherited_fields = inherited_fields if inherited_fields else None
        resp.overridden_fields = overridden_fields if overridden_fields else None
        return resp

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
        """Return merchant kit + all offer kits under that merchant."""
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
