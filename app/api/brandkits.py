import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationDep
from app.application.brandkit_service import BrandKitLinkService, BrandKitService
from app.database import get_db
from app.schemas.brandkit import (
    BrandKitAssetLinkCreate,
    BrandKitAssetLinkResponse,
    BrandKitCreate,
    BrandKitResponse,
    BrandKitUpdate,
)
from app.schemas.common import PaginatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/brandkits", tags=["brandkits"])


@router.post("", response_model=BrandKitResponse, status_code=201)
async def create_brandkit(data: BrandKitCreate, db: AsyncSession = Depends(get_db)):
    svc = BrandKitService(db)
    return await svc.create(data)


@router.get("", response_model=PaginatedResponse[BrandKitResponse])
async def list_brandkits(
    pagination: PaginationDep,
    scope_type: str | None = Query(None),
    scope_id: uuid.UUID | None = Query(None),
    merchant_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    svc = BrandKitService(db)
    items, total = await svc.list(
        scope_type=scope_type, scope_id=scope_id, merchant_id=merchant_id, **pagination
    )
    return PaginatedResponse(items=items, total=total, **pagination)


@router.get("/for-merchant/{merchant_id}")
async def list_for_merchant(merchant_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = BrandKitService(db)
    return await svc.list_for_merchant(merchant_id)


@router.get("/{kit_id}", response_model=BrandKitResponse)
async def get_brandkit(kit_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = BrandKitService(db)
    return await svc.get(kit_id)


@router.get("/{kit_id}/merged", response_model=BrandKitResponse)
async def get_brandkit_merged(kit_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = BrandKitService(db)
    return await svc.get_merged(kit_id)


@router.patch("/{kit_id}", response_model=BrandKitResponse)
async def update_brandkit(
    kit_id: uuid.UUID, data: BrandKitUpdate, db: AsyncSession = Depends(get_db)
):
    svc = BrandKitService(db)
    return await svc.update(kit_id, data)


@router.delete("/{kit_id}", status_code=204)
async def delete_brandkit(kit_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = BrandKitService(db)
    await svc.delete(kit_id)


# ── AI Extract ───────────────────────────────────────────────


@router.post("/{kit_id}/extract-profile")
async def extract_profile(
    kit_id: uuid.UUID,
    url: str | None = Form(None),
    file: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
):
    """Extract brand profile fields from a URL or uploaded document via AI."""
    from app.adapters.ai import get_ai_adapter
    from app.api.ai import _extract_docx_text, _extract_pdf_text, _extract_url_text

    text = ""
    if file and file.filename:
        content = await file.read()
        filename = file.filename.lower()
        if filename.endswith(".pdf"):
            text = _extract_pdf_text(content)
        elif filename.endswith((".docx", ".doc")):
            text = _extract_docx_text(content)
        else:
            text = content.decode("utf-8", errors="ignore")
    elif url:
        text = await _extract_url_text(url)
    else:
        return {"error": "Please provide a URL or upload a file"}, 400

    if not text.strip():
        return {"error": "Failed to extract text content"}

    adapter = await get_ai_adapter(db, scene_key="brandkit_extract", model_type="text_llm")
    try:
        profiles = await adapter.extract_brandkit_profiles(text)
    except RuntimeError as e:
        msg = str(e)
        if msg == "NO_LLM_CONFIGURED":
            return {"error": "NO_LLM_CONFIGURED"}
        return {"error": msg}
    return profiles


# ── Asset Links ──────────────────────────────────────────────


@router.post("/{kit_id}/asset-links", response_model=BrandKitAssetLinkResponse, status_code=201)
async def create_asset_link(
    kit_id: uuid.UUID, data: BrandKitAssetLinkCreate, db: AsyncSession = Depends(get_db)
):
    svc = BrandKitLinkService(db)
    return await svc.create(kit_id, data)


@router.get("/{kit_id}/asset-links", response_model=PaginatedResponse[BrandKitAssetLinkResponse])
async def list_asset_links(
    kit_id: uuid.UUID, pagination: PaginationDep, db: AsyncSession = Depends(get_db)
):
    svc = BrandKitLinkService(db)
    items, total = await svc.list(kit_id, **pagination)
    return PaginatedResponse(items=items, total=total, **pagination)


@router.delete("/{kit_id}/asset-links/{link_id}", status_code=204)
async def delete_asset_link(
    kit_id: uuid.UUID, link_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    svc = BrandKitLinkService(db)
    await svc.delete(kit_id, link_id)
