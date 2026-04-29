from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.application.media_provider_service import (
    activate_media_provider_config,
    create_media_provider_config,
    delete_media_provider_config,
    list_all_avatars_for_config,
    list_all_voices_for_config,
    list_avatar_tags_for_config,
    list_media_provider_configs,
    list_voice_tags_for_config,
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
    TagCategory,
    VoiceItem,
    VoicePreviewRequest,
    VoicePreviewResponse,
)

router = APIRouter(prefix="/media-providers", tags=["media-providers"])


# ── Catalog (provider capabilities + AI creation models) ────────────


@router.get("/catalog")
async def get_catalog(lang: str = Query("zh", pattern="^(zh|en)$")):
    """Public capability catalog — describes what each provider gives
    users when connected. Powers the rich provider cards in
    `/setting.html?section=media-providers`. Static / cache-friendly:
    no DB access, no credentials lookup.

    Shape: `{providers: [{key, capabilities: [...], featured_models?: [...]}]}`.
    """
    from app.adapters.video.chanjing_models import (
        IMAGE_MODELS,
        VIDEO_MODELS,
    )

    is_zh = lang.startswith("zh")

    # ── Chanjing capability summary ────────────────────────────────
    chanjing_video = [m.to_public_dict(lang) for m in VIDEO_MODELS]
    chanjing_image = [m.to_public_dict(lang) for m in IMAGE_MODELS]
    chanjing = {
        "key": "chanjing",
        "capabilities": [
            {
                "id": "digital_humans",
                "title": "数字人形象库" if is_zh else "Digital human library",
                "summary": "100+ 蝉镜真人定制形象，覆盖商务 / 时尚 / 居家 / 户外多场景"
                if is_zh else
                "100+ chanjing-trained avatars across business, fashion, lifestyle, outdoor scenes",
                "icon": "👤",
            },
            {
                "id": "voice_library",
                "title": "中文音色库" if is_zh else "Chinese voice library",
                "summary": "丰富中文音色 + 情感分类 + 语种标签 + 试听预览"
                if is_zh else
                "Rich Chinese voice library with emotion categories, language tags, and live preview",
                "icon": "🎤",
            },
            {
                "id": "lipsync_video",
                "title": "口播视频合成" if is_zh else "Lip-sync video synthesis",
                "summary": "脚本 → 音频 → 嘴型同步 → 成片，支持 9:16 / 16:9 / 1:1，B-roll 插入，字幕烧录"
                if is_zh else
                "Script → audio → lip-sync → final video. 9:16 / 16:9 / 1:1, B-roll insertion, burned-in captions",
                "icon": "🎬",
            },
            {
                "id": "ai_video_creation",
                "title": "AI 视频生成" if is_zh else "AI video creation",
                "summary": f"{len(chanjing_video)} 个文生视频 / 图生视频模型 —— HappyHorse 1.0、Kling 2.5、Doubao Seedance Pro、Hailuo 02、Vidu Q1"
                if is_zh else
                f"{len(chanjing_video)} text-to-video / image-to-video models — HappyHorse 1.0, Kling 2.5, Doubao Seedance Pro, Hailuo 02, Vidu Q1",
                "icon": "🎥",
                "badge": "new",
            },
            {
                "id": "ai_image_creation",
                "title": "AI 图像生成" if is_zh else "AI image creation",
                "summary": f"{len(chanjing_image)} 个文生图 / 图生图模型 —— Seedream 4.5（4K）、可灵 2.1、Wan 2.2"
                if is_zh else
                f"{len(chanjing_image)} text-to-image / image-to-image models — Seedream 4.5 (4K), Kling 2.1, Wan 2.2",
                "icon": "🖼️",
                "badge": "new",
            },
        ],
        "featured_models": [
            m.to_public_dict(lang) for m in VIDEO_MODELS if "flagship" in m.badges or "new" in m.badges
        ][:4],
        "all_models": {
            "video": chanjing_video,
            "image": chanjing_image,
        },
    }

    jogg = {
        "key": "jogg",
        "capabilities": [
            {
                "id": "english_avatars",
                "title": "英文 Avatar 库" if is_zh else "English avatar library",
                "summary": "数百个英文真人 / AI Avatar，覆盖年龄 / 性别 / 风格 / 比例"
                if is_zh else
                "Hundreds of real / AI-generated English avatars across age, gender, style, ratio",
                "icon": "👥",
            },
            {
                "id": "english_voices",
                "title": "多语种音色" if is_zh else "Multi-language voices",
                "summary": "2000+ 音色，覆盖英文 / 西语 / 法语 / 日语 / 韩语等主要市场"
                if is_zh else
                "2000+ voices spanning English, Spanish, French, Japanese, Korean and other major markets",
                "icon": "🎙️",
            },
            {
                "id": "lipsync_video",
                "title": "AI 口播视频" if is_zh else "AI talking-head video",
                "summary": "脚本驱动数字人口播，支持 9:16 / 16:9 / 1:1，画面背景替换"
                if is_zh else
                "Script-driven digital human videos. 9:16 / 16:9 / 1:1, background replacement",
                "icon": "🎬",
            },
            {
                "id": "video_translation",
                "title": "视频翻译" if is_zh else "Video translation",
                "summary": "AI 跨语种翻译 + 嘴型重对齐 —— 一个素材跑全球"
                if is_zh else
                "AI cross-lingual translation + lip re-sync — one asset, global reach",
                "icon": "🌍",
            },
        ],
    }

    google = {
        "key": "google",
        "capabilities": [
            {
                "id": "veo3_video",
                "title": "Veo 3 视频生成" if is_zh else "Veo 3 video generation",
                "summary": "Google 旗舰文生视频 —— 高一致性物理模拟、长镜头叙事"
                if is_zh else
                "Google's flagship text-to-video — high-fidelity physics, long-shot narratives",
                "icon": "🎥",
            },
            {
                "id": "nano_banana_image",
                "title": "Nano Banana 图像生成" if is_zh else "Nano Banana image generation",
                "summary": "Gemini 2.5 Flash Image —— 编辑型 T2I + I2I 极速出图"
                if is_zh else
                "Gemini 2.5 Flash Image — fast edit-style T2I + I2I",
                "icon": "🍌",
            },
            {
                "id": "shared_key",
                "title": "复用 Gemini Key" if is_zh else "Shared Gemini key",
                "summary": "无需独立账号 —— 直接复用 LLM 设置里的 Gemini API Key"
                if is_zh else
                "No separate account needed — reuses your Gemini API key from LLM settings",
                "icon": "🔗",
            },
        ],
    }

    return {"providers": [chanjing, jogg, google]}


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
    sort: str = Query("latest", pattern="^(latest|hottest|default)$"),
    db: AsyncSession = Depends(get_db),
):
    # The web picker has no pagination UI — return the provider's full
    # library (walking pages where the adapter supports it). MCP keeps
    # its own paginated path via the list_avatars tool.
    # ``sort`` is chanjing-specific (jogg silently ignores). "default"
    # means "no sort param" — chanjing then returns ID-ascending.
    sort_arg = None if sort == "default" else sort
    return await list_all_avatars_for_config(db, config_id, sort=sort_arg)


@router.get("/{config_id}/voices", response_model=list[VoiceItem])
async def list_voices(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    # Cap voice walk at 2 pages — picker UX needs ~100-200 voices for
    # variety, search-by-name covers the long tail. Without this jogg's
    # 2000+ voice library serializes 20 upstream HTTP calls and stalls
    # the modal for 10-30s. MCP's paginated /list_voices_for_config
    # path is untouched (it lets agents walk explicitly).
    return await list_all_voices_for_config(db, config_id, max_pages=2)


@router.get("/{config_id}/avatar-tags", response_model=list[TagCategory])
async def list_avatar_tags(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Provider-side tag dictionary for the avatar picker — used to
    render filter chips. Providers without a tag taxonomy return []."""
    return await list_avatar_tags_for_config(db, config_id)


@router.get("/{config_id}/voice-tags", response_model=list[TagCategory])
async def list_voice_tags(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await list_voice_tags_for_config(db, config_id)


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
