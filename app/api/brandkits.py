import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationDep
from app.application.brandkit_service import (
    BrandKitColorService,
    BrandKitFontService,
    BrandKitLinkService,
    BrandKitService,
)
from app.database import get_db
from app.schemas.brandkit import (
    BrandKitAssetLinkCreate,
    BrandKitAssetLinkResponse,
    BrandKitColorCreate,
    BrandKitColorResponse,
    BrandKitColorUpdate,
    BrandKitCreate,
    BrandKitFontCreate,
    BrandKitFontResponse,
    BrandKitFontUpdate,
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


# ── Colors ───────────────────────────────────────────────────


@router.get("/{kit_id}/colors", response_model=list[BrandKitColorResponse])
async def list_colors(kit_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = BrandKitColorService(db)
    return await svc.list(kit_id)


@router.post("/{kit_id}/colors", response_model=BrandKitColorResponse, status_code=201)
async def create_color(
    kit_id: uuid.UUID, data: BrandKitColorCreate, db: AsyncSession = Depends(get_db)
):
    svc = BrandKitColorService(db)
    return await svc.create(kit_id, data)


@router.patch("/{kit_id}/colors/{color_id}", response_model=BrandKitColorResponse)
async def update_color(
    kit_id: uuid.UUID, color_id: uuid.UUID, data: BrandKitColorUpdate,
    db: AsyncSession = Depends(get_db),
):
    svc = BrandKitColorService(db)
    return await svc.update(kit_id, color_id, data)


@router.delete("/{kit_id}/colors/{color_id}", status_code=204)
async def delete_color(
    kit_id: uuid.UUID, color_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    svc = BrandKitColorService(db)
    await svc.delete(kit_id, color_id)


# ── Fonts ────────────────────────────────────────────────────


@router.get("/{kit_id}/fonts", response_model=list[BrandKitFontResponse])
async def list_fonts(kit_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = BrandKitFontService(db)
    return await svc.list(kit_id)


@router.post("/{kit_id}/fonts", response_model=BrandKitFontResponse, status_code=201)
async def create_font(
    kit_id: uuid.UUID, data: BrandKitFontCreate, db: AsyncSession = Depends(get_db)
):
    svc = BrandKitFontService(db)
    return await svc.create(kit_id, data)


@router.patch("/{kit_id}/fonts/{font_id}", response_model=BrandKitFontResponse)
async def update_font(
    kit_id: uuid.UUID, font_id: uuid.UUID, data: BrandKitFontUpdate,
    db: AsyncSession = Depends(get_db),
):
    svc = BrandKitFontService(db)
    return await svc.update(kit_id, font_id, data)


@router.delete("/{kit_id}/fonts/{font_id}", status_code=204)
async def delete_font(
    kit_id: uuid.UUID, font_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    svc = BrandKitFontService(db)
    await svc.delete(kit_id, font_id)


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


# ── AI Suggest Brand Voice ───────────────────────────────────


@router.post("/{kit_id}/suggest-voice")
async def suggest_brand_voice(
    kit_id: uuid.UUID,
    url: str | None = Form(None),
    file: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
):
    """Draft a ``brand_voice`` string from a brand document (PPT / PDF / docx /
    xlsx / txt) OR a brand-intro URL. Does NOT write to the kit — returns the
    suggestion so the user can review + edit before saving.

    Reuses ``extract_text_from_source`` (same pipeline as offer KB smart-update)
    for input normalization. The LLM step is a single call producing a 3-5
    paragraph voice spec — structured text, not the seven dead JSONB fields
    the old ``/extract-profile`` endpoint chased.
    """
    from app.adapters.ai import get_ai_adapter
    from app.api.ai import _friendly_llm_error, extract_text_from_source

    text, source, filename = await extract_text_from_source(
        file=file, url=url, context_label=f"suggest-voice kit={kit_id}",
    )
    source_label = filename if source == "file" else (url or "")

    adapter = await get_ai_adapter(db, scene_key="brandkit_extract", model_type="text_llm")
    try:
        voice = await adapter.suggest_brand_voice(text)
    except RuntimeError as e:
        if str(e) == "NO_LLM_CONFIGURED":
            return JSONResponse(status_code=503, content={"error": "NO_LLM_CONFIGURED"})
        logger.error(
            "suggest-voice failed | kit=%s source=%s text_len=%d | %s",
            kit_id, source_label, len(text), e, exc_info=True,
        )
        return JSONResponse(status_code=502, content={"error": _friendly_llm_error(e, adapter)})
    except Exception as e:
        logger.error(
            "suggest-voice failed | kit=%s source=%s text_len=%d | %s",
            kit_id, source_label, len(text), e, exc_info=True,
        )
        return JSONResponse(status_code=502, content={"error": _friendly_llm_error(e, adapter)})

    return {"brand_voice": voice}


@router.post("/{kit_id}/asset-links/{link_id}/set-primary", response_model=BrandKitAssetLinkResponse)
async def set_asset_link_primary(
    kit_id: uuid.UUID, link_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Promote this link to the primary slot within its role — used for logos
    (one primary, several alternates). Demotes the previous primary."""
    svc = BrandKitLinkService(db)
    return await svc.set_primary(kit_id, link_id)
