from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.asset_parser import LocalAssetParser, LocalMetadataExtractor
from app.adapters.storage import LocalStorageAdapter
from app.api.deps import PaginationDep
from app.application.asset_service import AssetService
from app.database import async_session_factory, get_db
from app.domain.enums import AssetType, ScopeType
from app.schemas.asset import (
    AssetCopyCreate,
    AssetProcessingJobResponse,
    AssetResponse,
    AssetUpdate,
    AssetUploadMeta,
    DuplicateCheckResponse,
    TagAnalyticsResponse,
    TagAnalyticsItem,
)
from app.schemas.asset_slice import AssetSliceResponse
from app.schemas.common import PaginatedResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assets", tags=["assets"])


def get_storage() -> LocalStorageAdapter:
    return LocalStorageAdapter()


@router.post("/upload", response_model=AssetResponse, status_code=201)
async def upload_asset(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    scope_type: ScopeType = Form(...),
    scope_id: uuid.UUID = Form(...),
    asset_type: AssetType = Form(...),
    language: str = Form("zh-CN"),
    db: AsyncSession = Depends(get_db),
    storage: LocalStorageAdapter = Depends(get_storage),
):
    content = await file.read()
    meta = AssetUploadMeta(
        scope_type=scope_type,
        scope_id=scope_id,
        asset_type=asset_type,
        language=language,
    )
    svc = AssetService(db, storage)
    asset = await svc.upload(content, file.filename or "unnamed", file.content_type, meta)

    # Commit NOW so the background task (which opens its own session) can find the asset
    await db.commit()
    await db.refresh(asset)

    # Auto-trigger background parse
    background_tasks.add_task(_parse_in_background, asset.id)
    return asset


@router.post("/copy", response_model=AssetResponse, status_code=201)
async def create_copy_asset(
    data: AssetCopyCreate,
    db: AsyncSession = Depends(get_db),
):
    storage = get_storage()
    svc = AssetService(db, storage)
    asset = await svc.create_copy(data)
    await db.commit()
    await db.refresh(asset)
    return asset


@router.get("/check-duplicate", response_model=DuplicateCheckResponse)
async def check_duplicate(
    hash: str = Query(..., description="SHA-256 hex digest of the file"),
    scope_type: str | None = Query(None),
    scope_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    storage = get_storage()
    svc = AssetService(db, storage)
    asset = await svc.check_duplicate(hash, scope_type=scope_type, scope_id=scope_id)
    return DuplicateCheckResponse(exists=asset is not None, asset=asset)


@router.get("/search", response_model=PaginatedResponse[AssetResponse])
async def search_assets(
    q: str | None = Query(None),
    asset_type: str | None = Query(None),
    tags: str | None = Query(None, description="Comma-separated tag list"),
    status: str | None = Query(None),
    scope_type: str | None = Query(None),
    scope_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    storage = get_storage()
    svc = AssetService(db, storage)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    items, total = await svc.search(
        q=q,
        asset_type=asset_type,
        tags=tag_list,
        status=status,
        scope_type=scope_type,
        scope_id=scope_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/highlights", response_model=PaginatedResponse[AssetSliceResponse])
async def get_highlights(
    scope_type: str | None = Query(None),
    scope_id: uuid.UUID | None = Query(None),
    min_hook_score: float = Query(0.0, ge=0.0, le=1.0),
    min_proof_score: float = Query(0.0, ge=0.0, le=1.0),
    min_reuse_score: float = Query(0.0, ge=0.0, le=1.0),
    slice_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    storage = get_storage()
    svc = AssetService(db, storage)
    items, total = await svc.get_highlights(
        scope_type=scope_type,
        scope_id=scope_id,
        min_hook_score=min_hook_score,
        min_proof_score=min_proof_score,
        min_reuse_score=min_reuse_score,
        slice_type=slice_type,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/tag-analytics", response_model=TagAnalyticsResponse)
async def get_tag_analytics(
    scope_type: str | None = Query(None),
    scope_id: uuid.UUID | None = Query(None),
    asset_type: str | None = Query(None),
    category: str | None = Query(None, description="Filter by tag category (e.g. subject, usage, selling_point)"),
    db: AsyncSession = Depends(get_db),
):
    storage = get_storage()
    svc = AssetService(db, storage)
    rows = await svc.get_tag_analytics(
        scope_type=scope_type,
        scope_id=scope_id,
        asset_type=asset_type,
        category=category,
    )
    return TagAnalyticsResponse(items=[TagAnalyticsItem(**r) for r in rows])


@router.get("", response_model=PaginatedResponse[AssetResponse])
async def list_assets(
    pagination: PaginationDep,
    scope_type: str | None = Query(None),
    scope_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    storage = get_storage()
    svc = AssetService(db, storage)
    items, total = await svc.list(scope_type=scope_type, scope_id=scope_id, **pagination)
    return PaginatedResponse(items=items, total=total, **pagination)


@router.get("/{asset_id}/thumbnail")
async def get_thumbnail(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    storage = get_storage()
    svc = AssetService(db, storage)
    asset = await svc.get(asset_id)
    if not asset.preview_uri:
        raise HTTPException(status_code=404, detail="Thumbnail not available")
    path = storage.get_absolute_path(asset.preview_uri)
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{asset_id}/file")
async def get_file(
    asset_id: uuid.UUID,
    download: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    storage = get_storage()
    svc = AssetService(db, storage)
    asset = await svc.get(asset_id)
    if not asset.storage_uri:
        raise HTTPException(status_code=404, detail="File not available")
    path = storage.get_absolute_path(asset.storage_uri)
    if download:
        return FileResponse(path, media_type=asset.mime_type or "application/octet-stream", filename=asset.file_name)
    return FileResponse(path, media_type=asset.mime_type or "application/octet-stream")


@router.patch("/{asset_id}", response_model=AssetResponse)
async def update_asset(
    asset_id: uuid.UUID,
    data: AssetUpdate,
    db: AsyncSession = Depends(get_db),
):
    storage = get_storage()
    svc = AssetService(db, storage)
    return await svc.update_asset(asset_id, title=data.title, tags_json=data.tags_json)


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    storage = get_storage()
    svc = AssetService(db, storage)
    await svc.delete_asset(asset_id)


@router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    storage = get_storage()
    svc = AssetService(db, storage)
    return await svc.get(asset_id)


@router.get("/{asset_id}/processing-jobs", response_model=list[AssetProcessingJobResponse])
async def list_processing_jobs(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    storage = get_storage()
    svc = AssetService(db, storage)
    return await svc.get_processing_jobs(asset_id)


async def _parse_in_background(asset_id: uuid.UUID) -> None:
    """Background task: opens its own DB session and runs parse pipeline."""
    storage = get_storage()
    extractor = LocalMetadataExtractor()
    parser = LocalAssetParser(extractor)
    async with async_session_factory() as session:
        try:
            svc = AssetService(session, storage)
            await svc.run_parse(asset_id, extractor, parser)
        except Exception:
            logger.exception("Background parse failed for asset %s", asset_id)


@router.post("/{asset_id}/parse", response_model=AssetResponse)
async def trigger_parse(
    asset_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    storage = get_storage()
    svc = AssetService(db, storage)
    asset = await svc.get(asset_id)

    background_tasks.add_task(_parse_in_background, asset_id)
    return asset


@router.get("/{asset_id}/slices", response_model=list[AssetSliceResponse])
async def list_slices(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    storage = get_storage()
    svc = AssetService(db, storage)
    return await svc.get_slices(asset_id)
