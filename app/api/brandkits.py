import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse
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
    """Extract brand profile fields from a URL or uploaded document via AI.

    Delegates text extraction + normalization to the shared
    ``extract_text_from_source`` helper so this endpoint supports the
    exact same set of formats (including PPTX) with the same dedup +
    size cap the KB path uses. Historical drift between the two paths
    silently broke PPTX on this endpoint; the shared helper prevents
    that recurring.
    """
    from app.adapters.ai import get_ai_adapter
    from app.api.ai import _friendly_llm_error, extract_text_from_source

    # Lets HTTPException (400 unsupported format / 413 oversized / 400
    # empty / 400 missing input) propagate as proper HTTP errors.
    text, source, filename = await extract_text_from_source(
        file=file, url=url, context_label=f"extract-profile kit={kit_id}",
    )
    source_label = filename if source == "file" else (url or "")

    adapter = await get_ai_adapter(db, scene_key="brandkit_extract", model_type="text_llm")
    try:
        profiles = await adapter.extract_brandkit_profiles(text)
    except RuntimeError as e:
        # Feature requires config the operator hasn't done yet — 503
        # Service Unavailable is semantically right and the frontend
        # matches exactly on `data.error === "NO_LLM_CONFIGURED"` to
        # render its "configure LLM" CTA, so keep the string stable.
        if str(e) == "NO_LLM_CONFIGURED":
            return JSONResponse(status_code=503, content={"error": "NO_LLM_CONFIGURED"})
        logger.error(
            "extract-profile failed | kit=%s source=%s text_len=%d | %s",
            kit_id, source_label, len(text), e,
            exc_info=True,
        )
        return JSONResponse(
            status_code=502,
            content={"error": _friendly_llm_error(e, adapter)},
        )
    except Exception as e:
        # Upstream LLM error (timeout / connect failure / rate limit /
        # auth / bad-request). Return 502 Bad Gateway with the friendly
        # diagnostic string — same error surface the KB infer path uses.
        # Previously this was swallowed as HTTP 200 with error payload,
        # which made the browser Network tab look successful and hid the
        # real problem from operators during triage.
        logger.error(
            "extract-profile failed | kit=%s source=%s text_len=%d | %s",
            kit_id, source_label, len(text), e,
            exc_info=True,
        )
        return JSONResponse(
            status_code=502,
            content={"error": _friendly_llm_error(e, adapter)},
        )
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
