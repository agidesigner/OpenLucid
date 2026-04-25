import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError
from app.infrastructure.merchant_repo import MerchantRepository
from app.models.merchant import Merchant
from app.schemas.merchant import MerchantCreate, MerchantUpdate


class MerchantService:
    def __init__(self, session: AsyncSession):
        self.repo = MerchantRepository(session)

    async def create(self, data: MerchantCreate) -> Merchant:
        return await self.repo.create(**data.model_dump())

    async def get(self, merchant_id: uuid.UUID) -> Merchant:
        merchant = await self.repo.get_by_id(merchant_id)
        if not merchant:
            raise NotFoundError("Merchant", str(merchant_id))
        return merchant

    async def list(self, page: int = 1, page_size: int = 20) -> tuple[list[Merchant], int]:
        offset = (page - 1) * page_size
        return await self.repo.list(offset=offset, limit=page_size)

    async def update(self, merchant_id: uuid.UUID, data: MerchantUpdate) -> Merchant:
        merchant = await self.get(merchant_id)
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return merchant
        return await self.repo.update(merchant, **update_data)

    async def delete(self, merchant_id: uuid.UUID) -> None:
        """Hard-delete a merchant and everything under it.

        Mirrors ``OfferService.delete``'s explicit-cascade discipline
        (the polymorphic ``(scope_type, scope_id)`` pointer used by
        ``knowledge_items`` / ``brandkits`` / ``assets`` is not a real
        FK, so DB cascades don't fire). For each offer under the
        merchant we delegate to ``OfferService.delete`` so the offer's
        own dependent rows (topic_plans, creations, strategy_units,
        offer-scoped knowledge/brandkits/assets) get the same
        treatment as a single-offer delete. Then we sweep the
        merchant-scoped rows and drop the merchant.

        Asset files on disk for merchant-scope assets are left in
        place — same trade-off the offer cascade made (see
        offer_service.delete docstring): orphaned bytes are cheaper
        than engineering a safe file-cleanup path through multiple
        services. The asset row is removed; only the file lingers.
        """
        from sqlalchemy import delete as sql_delete

        from app.application.offer_service import OfferService
        from app.models.asset import Asset
        from app.models.brandkit import BrandKit
        from app.models.knowledge_item import KnowledgeItem

        merchant = await self.get(merchant_id)
        session = self.repo.session

        offer_svc = OfferService(session)
        offers, _ = await offer_svc.list(merchant_id=merchant_id, page=1, page_size=1000)
        for offer in offers:
            await offer_svc.delete(offer.id)

        await session.execute(sql_delete(KnowledgeItem).where(
            KnowledgeItem.scope_type == "merchant",
            KnowledgeItem.scope_id == merchant_id,
        ))
        await session.execute(sql_delete(BrandKit).where(
            BrandKit.scope_type == "merchant",
            BrandKit.scope_id == merchant_id,
        ))
        await session.execute(sql_delete(Asset).where(
            Asset.scope_type == "merchant",
            Asset.scope_id == merchant_id,
        ))

        await self.repo.delete(merchant)
