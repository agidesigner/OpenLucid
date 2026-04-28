"""Service layer for media provider configs (Chanjing, Jogg, ...).

Mirrors `setting_service.py` LLM patterns, with one key difference:
**activation is per-provider** — at most one active config per provider type.
A user may legitimately want both Chanjing AND Jogg active simultaneously.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.video import get_video_provider
from app.exceptions import AppError, NotFoundError
from app.infrastructure.media_provider_repo import MediaProviderRepository
from app.models.media_provider_config import MediaProviderConfig
from app.schemas.media_provider import (
    AvatarItem,
    MediaProviderConfigCreate,
    MediaProviderConfigResponse,
    MediaProviderConfigUpdate,
    MediaProviderDefaults,
    TagCategory,
    TagOption,
    VoiceItem,
)

logger = logging.getLogger(__name__)

# Tag dictionaries change rarely (chanjing-side admin op); a cheap
# in-process TTL cache keyed by (config_id, kind) avoids hitting the
# upstream on every picker open. Cleared on process restart.
_TAG_CACHE_TTL_SECONDS = 3600.0
_TAG_CACHE: dict[tuple[uuid.UUID, str], tuple[float, list[TagCategory]]] = {}


# Required credential keys per provider — used for both validation and masking.
REQUIRED_CREDENTIAL_KEYS: dict[str, tuple[str, ...]] = {
    "chanjing": ("app_id", "secret_key"),
    "jogg": ("api_key",),
    "google": ("api_key",),
}


# ── Helpers ─────────────────────────────────────────────────────────


def _validate_credentials_shape(provider: str, credentials: dict) -> None:
    """Raise AppError if required keys are missing or empty."""
    required = REQUIRED_CREDENTIAL_KEYS.get(provider)
    if not required:
        raise AppError(
            "UNKNOWN_PROVIDER",
            f"Unknown media provider: {provider!r}",
            400,
        )
    missing = [k for k in required if not str(credentials.get(k, "")).strip()]
    if missing:
        raise AppError(
            "MISSING_CREDENTIALS",
            f"{provider} requires fields: {', '.join(missing)}",
            400,
        )


def _to_response(config: MediaProviderConfig) -> MediaProviderConfigResponse:
    # Only return the keys the provider actually expects — keeps legacy
    # fields (from old schemas) out of the response surface.
    required_keys = REQUIRED_CREDENTIAL_KEYS.get(config.provider) or tuple((config.credentials or {}).keys())
    credentials = {k: str((config.credentials or {}).get(k, "")) for k in required_keys}
    return MediaProviderConfigResponse(
        id=str(config.id),
        provider=config.provider,
        label=config.label,
        credentials=credentials,
        defaults=MediaProviderDefaults(**(config.defaults or {})),
        is_active=config.is_active,
    )


# ── CRUD ────────────────────────────────────────────────────────────


async def list_media_provider_configs(
    db: AsyncSession,
) -> list[MediaProviderConfigResponse]:
    repo = MediaProviderRepository(db)
    configs = await repo.list_all()
    return [_to_response(c) for c in configs]


async def create_media_provider_config(
    db: AsyncSession, data: MediaProviderConfigCreate
) -> MediaProviderConfigResponse:
    _validate_credentials_shape(data.provider, data.credentials)

    repo = MediaProviderRepository(db)

    # New config of this provider type becomes active; demote existing active of
    # the SAME provider only (cross-provider activation is independent).
    existing_active = await repo.get_active_by_provider(data.provider)
    if existing_active:
        existing_active.is_active = False

    config = await repo.create(
        provider=data.provider,
        label=data.label,
        credentials=dict(data.credentials),
        defaults=data.defaults.model_dump() if data.defaults else {},
        is_active=True,
    )
    await db.commit()
    await db.refresh(config)
    return _to_response(config)


async def update_media_provider_config(
    db: AsyncSession,
    config_id: uuid.UUID,
    data: MediaProviderConfigUpdate,
) -> MediaProviderConfigResponse:
    repo = MediaProviderRepository(db)
    config = await repo.get_by_id(config_id)
    if not config:
        raise NotFoundError("MediaProviderConfig", str(config_id))

    if data.label is not None:
        config.label = data.label
    if data.credentials is not None:
        # Merge: allow partial credential updates (e.g. user only changes secret_key).
        # If a value is empty string, keep the old one (UI sends "" to mean "unchanged").
        merged = dict(config.credentials or {})
        for k, v in data.credentials.items():
            if v:  # only override if non-empty
                merged[k] = v
        _validate_credentials_shape(config.provider, merged)
        config.credentials = merged
    if data.defaults is not None:
        config.defaults = data.defaults.model_dump()

    await db.commit()
    await db.refresh(config)
    # Credential change may switch us to a different chanjing tenant —
    # cached tag dictionaries from the old account would be stale.
    _invalidate_tag_cache(config_id)
    return _to_response(config)


async def delete_media_provider_config(
    db: AsyncSession, config_id: uuid.UUID
) -> None:
    repo = MediaProviderRepository(db)
    config = await repo.get_by_id(config_id)
    if not config:
        raise NotFoundError("MediaProviderConfig", str(config_id))

    was_active = config.is_active
    provider = config.provider
    await repo.delete(config)
    _invalidate_tag_cache(config_id)

    # If we deleted the active config of this provider, promote another of the
    # same provider type if one exists.
    if was_active:
        siblings = await repo.list_by_provider(provider)
        if siblings:
            siblings[0].is_active = True

    await db.commit()


async def activate_media_provider_config(
    db: AsyncSession, config_id: uuid.UUID
) -> MediaProviderConfigResponse:
    repo = MediaProviderRepository(db)
    config = await repo.get_by_id(config_id)
    if not config:
        raise NotFoundError("MediaProviderConfig", str(config_id))

    # Demote other configs of the SAME provider only.
    for sibling in await repo.list_by_provider(config.provider):
        sibling.is_active = sibling.id == config.id

    await db.commit()
    await db.refresh(config)
    return _to_response(config)


# ── Provider proxy: avatar/voice listing + validation ──────────────


async def validate_credentials(provider: str, credentials: dict) -> None:
    """Test credentials by making a minimal call (list_avatars page=1 size=1).

    Raises AppError on any failure (auth, network, malformed response).
    """
    _validate_credentials_shape(provider, credentials)
    video_provider = get_video_provider(provider, credentials)
    # If this fails it'll raise AppError with a useful message
    avatars = await video_provider.list_avatars(page=1, page_size=1)
    if not isinstance(avatars, list):
        raise AppError(
            "VALIDATION_FAILED",
            f"{provider} returned unexpected response from list_avatars",
            502,
        )


async def list_avatars_for_config(
    db: AsyncSession,
    config_id: uuid.UUID,
    page: int = 1,
    page_size: int = 50,
) -> list[AvatarItem]:
    repo = MediaProviderRepository(db)
    config = await repo.get_by_id(config_id)
    if not config:
        raise NotFoundError("MediaProviderConfig", str(config_id))
    video_provider = get_video_provider(config.provider, config.credentials or {})
    avatars = await video_provider.list_avatars(page=page, page_size=page_size)
    return [
        AvatarItem(
            id=a.id,
            name=a.name,
            gender=a.gender,
            age=a.age,
            preview_image_url=a.preview_image_url,
            preview_video_url=a.preview_video_url,
            extras=a.extras or {},
        )
        for a in avatars
    ]


async def list_all_avatars_for_config(
    db: AsyncSession,
    config_id: uuid.UUID,
    sort: str | None = None,
) -> list[AvatarItem]:
    """Return the provider's full avatar library, walking pagination if
    the adapter supports it. Used by the web picker, which has no UI for
    paging and wants the full list up-front. ``sort`` is forwarded to
    providers that support it (chanjing); jogg ignores it."""
    repo = MediaProviderRepository(db)
    config = await repo.get_by_id(config_id)
    if not config:
        raise NotFoundError("MediaProviderConfig", str(config_id))
    video_provider = get_video_provider(config.provider, config.credentials or {})
    fetch_all = getattr(video_provider, "list_all_avatars", None)
    if callable(fetch_all):
        avatars = await fetch_all(sort=sort) if sort is not None else await fetch_all()
    else:
        avatars = await video_provider.list_avatars(page=1, page_size=200)
    return [
        AvatarItem(
            id=a.id,
            name=a.name,
            gender=a.gender,
            age=a.age,
            preview_image_url=a.preview_image_url,
            preview_video_url=a.preview_video_url,
            extras=a.extras or {},
        )
        for a in avatars
    ]


async def list_voices_for_config(
    db: AsyncSession,
    config_id: uuid.UUID,
    page: int = 1,
    page_size: int = 50,
) -> list[VoiceItem]:
    repo = MediaProviderRepository(db)
    config = await repo.get_by_id(config_id)
    if not config:
        raise NotFoundError("MediaProviderConfig", str(config_id))
    video_provider = get_video_provider(config.provider, config.credentials or {})
    voices = await video_provider.list_voices(page=page, page_size=page_size)
    return [
        VoiceItem(
            id=v.id,
            name=v.name,
            gender=v.gender,
            age=v.age,
            language=v.language,
            sample_url=v.sample_url,
            extras=v.extras or {},
        )
        for v in voices
    ]


async def list_all_voices_for_config(
    db: AsyncSession,
    config_id: uuid.UUID,
    max_pages: int | None = None,
) -> list[VoiceItem]:
    """Voice counterpart to list_all_avatars_for_config — walk pages
    when the adapter supports it, fall back to a single oversized page
    otherwise. ``max_pages`` lets callers cap the walk: jogg has 2000+
    public voices and walking the entire library on every modal-open
    serializes ~20 upstream HTTP calls, which the picker doesn't
    actually need — it only displays 50 at a time and supports search.
    """
    repo = MediaProviderRepository(db)
    config = await repo.get_by_id(config_id)
    if not config:
        raise NotFoundError("MediaProviderConfig", str(config_id))
    video_provider = get_video_provider(config.provider, config.credentials or {})
    fetch_all = getattr(video_provider, "list_all_voices", None)
    if callable(fetch_all):
        voices = (
            await fetch_all(max_pages=max_pages)
            if max_pages is not None
            else await fetch_all()
        )
    else:
        voices = await video_provider.list_voices(page=1, page_size=200)
    return [
        VoiceItem(
            id=v.id,
            name=v.name,
            gender=v.gender,
            age=v.age,
            language=v.language,
            sample_url=v.sample_url,
            extras=v.extras or {},
        )
        for v in voices
    ]


async def synthesize_voice_preview(
    db: AsyncSession,
    config_id: uuid.UUID,
    voice_id: str,
    text: str,
) -> str:
    """Generate a TTS audition clip and return the audio URL."""
    repo = MediaProviderRepository(db)
    config = await repo.get_by_id(config_id)
    if not config:
        raise NotFoundError("MediaProviderConfig", str(config_id))
    video_provider = get_video_provider(config.provider, config.credentials or {})
    return await video_provider.synthesize_speech(voice_id, text)


# ── Tag dictionary (cached) ─────────────────────────────────────────


def _normalize_tag_categories(raw: list[dict]) -> list[TagCategory]:
    return [
        TagCategory(
            id=str(cat["id"]),
            name=cat.get("name", ""),
            tags=[
                TagOption(
                    id=str(t["id"]),
                    name=t.get("name", ""),
                    parent_id=t.get("parent_id"),
                )
                for t in (cat.get("tags") or [])
            ],
        )
        for cat in raw
    ]


async def list_avatar_tags_for_config(
    db: AsyncSession, config_id: uuid.UUID,
) -> list[TagCategory]:
    return await _list_tags_cached(db, config_id, kind="avatar")


async def list_voice_tags_for_config(
    db: AsyncSession, config_id: uuid.UUID,
) -> list[TagCategory]:
    return await _list_tags_cached(db, config_id, kind="voice")


async def _list_tags_cached(
    db: AsyncSession, config_id: uuid.UUID, *, kind: str,
) -> list[TagCategory]:
    """Per-config in-process cache for tag dictionaries.

    Providers without a tag taxonomy (jogg, google) return [] — we still
    cache the empty result so we don't re-trigger the getattr+miss path
    on every picker open.
    """
    key = (config_id, kind)
    now = time.time()
    cached = _TAG_CACHE.get(key)
    if cached and (now - cached[0]) < _TAG_CACHE_TTL_SECONDS:
        return cached[1]
    repo = MediaProviderRepository(db)
    config = await repo.get_by_id(config_id)
    if not config:
        raise NotFoundError("MediaProviderConfig", str(config_id))
    video_provider = get_video_provider(config.provider, config.credentials or {})
    method = getattr(video_provider, f"list_{kind}_tags", None)
    if callable(method):
        raw = await method()
        result = _normalize_tag_categories(raw)
    else:
        result = []
    _TAG_CACHE[key] = (now, result)
    return result


def _invalidate_tag_cache(config_id: uuid.UUID) -> None:
    """Drop cached tag dictionaries for a config — call when credentials
    change or the config is deleted, so a future fetch picks up fresh
    data from a different upstream account."""
    for kind in ("avatar", "voice"):
        _TAG_CACHE.pop((config_id, kind), None)
