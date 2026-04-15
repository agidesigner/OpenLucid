from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationDep, get_db
from app.application.video_service import (
    create_video_job,
    delete_video_job,
    get_video_job,
    list_all_videos,
    list_video_jobs_for_creation,
)
from app.schemas.common import PaginatedResponse
from app.schemas.video import (
    VideoGenerateRequest,
    VideoJobResponse,
    VideoJobWithCreationResponse,
)

# Two routers because the resource sits under two prefixes:
#   POST/GET /api/v1/creations/{creation_id}/videos     (collection per creation)
#   GET/DELETE /api/v1/videos/{video_id}                (single job)

creations_videos_router = APIRouter(
    prefix="/creations/{creation_id}/videos",
    tags=["videos"],
)


@creations_videos_router.post("", response_model=VideoJobResponse, status_code=201)
async def create_for_creation(
    creation_id: uuid.UUID,
    data: VideoGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    return await create_video_job(db, creation_id, data)


@creations_videos_router.get("", response_model=list[VideoJobResponse])
async def list_for_creation(
    creation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await list_video_jobs_for_creation(db, creation_id)


videos_router = APIRouter(prefix="/videos", tags=["videos"])


@videos_router.get("", response_model=PaginatedResponse[VideoJobWithCreationResponse])
async def list_videos(
    pagination: PaginationDep,
    status: str | None = Query(None, description="Filter by status"),
    provider: str | None = Query(None, description="Filter by provider name"),
    offer_id: uuid.UUID | None = Query(None, description="Filter by offer (product)"),
    db: AsyncSession = Depends(get_db),
):
    """Cross-creation video listing for the Video Studio page."""
    items, total = await list_all_videos(
        db,
        status=status,
        provider=provider,
        offer_id=offer_id,
        **pagination,
    )
    return PaginatedResponse(items=items, total=total, **pagination)


@videos_router.get("/{video_id}", response_model=VideoJobResponse)
async def get_video(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a video job. Triggers a lazy refresh from the provider if non-terminal."""
    return await get_video_job(db, video_id)


@videos_router.delete("/{video_id}", status_code=204)
async def delete_video(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete the local job row. Does NOT delete the remote video on the provider."""
    await delete_video_job(db, video_id)
