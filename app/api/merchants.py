import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationDep
from app.application.merchant_service import MerchantService
from app.database import get_db
from app.schemas.common import PaginatedResponse
from app.schemas.merchant import MerchantCreate, MerchantResponse, MerchantUpdate

router = APIRouter(prefix="/merchants", tags=["merchants"])


@router.post("", response_model=MerchantResponse, status_code=201)
async def create_merchant(data: MerchantCreate, db: AsyncSession = Depends(get_db)):
    svc = MerchantService(db)
    merchant = await svc.create(data)
    return merchant


@router.get("", response_model=PaginatedResponse[MerchantResponse])
async def list_merchants(pagination: PaginationDep, db: AsyncSession = Depends(get_db)):
    svc = MerchantService(db)
    items, total = await svc.list(**pagination)
    return PaginatedResponse(items=items, total=total, **pagination)


@router.get("/{merchant_id}", response_model=MerchantResponse)
async def get_merchant(merchant_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = MerchantService(db)
    return await svc.get(merchant_id)


@router.patch("/{merchant_id}", response_model=MerchantResponse)
async def update_merchant(
    merchant_id: uuid.UUID, data: MerchantUpdate, db: AsyncSession = Depends(get_db)
):
    svc = MerchantService(db)
    return await svc.update(merchant_id, data)
