from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.application.media_provider_service import (
    activate_media_provider_config,
    create_media_provider_config,
    delete_media_provider_config,
    list_avatars_for_config,
    list_media_provider_configs,
    list_voices_for_config,
    synthesize_voice_preview,
    update_media_provider_config,
    validate_credentials,
)
from app.schemas.media_provider import (
    AvatarItem,
    MediaProviderConfigCreate,
    MediaProviderConfigResponse,
    MediaProviderConfigUpdate,
    MediaProviderValidateRequest,
    VoiceItem,
    VoicePreviewRequest,
    VoicePreviewResponse,
)

router = APIRouter(prefix="/media-providers", tags=["media-providers"])


# ── CRUD ────────────────────────────────────────────────────────────


@router.get("", response_model=list[MediaProviderConfigResponse])
async def list_configs(db: AsyncSession = Depends(get_db)):
    return await list_media_provider_configs(db)


@router.post("", response_model=MediaProviderConfigResponse, status_code=201)
async def create_config(
    data: MediaProviderConfigCreate, db: AsyncSession = Depends(get_db)
):
    return await create_media_provider_config(db, data)


@router.post("/validate")
async def validate_config(data: MediaProviderValidateRequest):
    """Test raw credentials before saving (no DB write)."""
    await validate_credentials(data.provider, data.credentials)
    return {"ok": True}


@router.put("/{config_id}", response_model=MediaProviderConfigResponse)
async def update_config(
    config_id: uuid.UUID,
    data: MediaProviderConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    return await update_media_provider_config(db, config_id, data)


@router.delete("/{config_id}", status_code=204)
async def delete_config(config_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await delete_media_provider_config(db, config_id)


@router.post("/{config_id}/activate", response_model=MediaProviderConfigResponse)
async def activate_config(
    config_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    return await activate_media_provider_config(db, config_id)


# ── Provider proxy: avatar / voice listing ──────────────────────────


@router.get("/{config_id}/avatars", response_model=list[AvatarItem])
async def list_avatars(
    config_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    return await list_avatars_for_config(db, config_id, page=page, page_size=page_size)


@router.get("/{config_id}/voices", response_model=list[VoiceItem])
async def list_voices(
    config_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    return await list_voices_for_config(db, config_id, page=page, page_size=page_size)


@router.post(
    "/{config_id}/voices/{voice_id}/preview",
    response_model=VoicePreviewResponse,
)
async def preview_voice(
    config_id: uuid.UUID,
    voice_id: str,
    data: VoicePreviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """Synthesize a TTS audition clip for the given voice + text.

    Used by the per-row "▶ audition" button in the Generate Video modal so the
    user can hear how their actual script sounds in each voice.
    """
    audio_url = await synthesize_voice_preview(db, config_id, voice_id, data.text)
    return VoicePreviewResponse(audio_url=audio_url)
