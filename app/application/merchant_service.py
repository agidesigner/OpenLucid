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
