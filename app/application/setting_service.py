from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException

logger = logging.getLogger(__name__)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_config import LLMConfig
from app.models.media_capability_default import MediaCapabilityDefault
from app.models.media_provider_config import MediaProviderConfig
from app.models.model_scene_config import ModelSceneConfig
from app.schemas.setting import (
    LLMConfigCreate,
    LLMConfigResponse,
    LLMConfigUpdate,
    LLMSceneConfigsResponse,
    LLMSceneConfigsUpdate,
    MODEL_TYPE_LABELS,
    MediaCapabilitiesResponse,
    MediaCapabilitiesUpdateRequest,
    MediaCapabilityConfig,
    MediaCapabilityOption,
    ModelTypeConfig,
    SceneSection,
    SYSTEM_SCENES,
)


def _to_response(config: LLMConfig) -> LLMConfigResponse:
    return LLMConfigResponse(
        id=str(config.id),
        label=config.label,
        provider=config.provider,
        api_key=config.api_key,
        base_url=config.base_url,
        model_name=config.model_name,
        is_active=config.is_active,
    )


# Marker label so we can recognize the auto-managed mirror row vs. a row a
# user might have hand-created (we never create one by hand today, but the
# label keeps intent obvious in the DB).
_GOOGLE_MIRROR_LABEL = "Google (linked from LLM Gemini)"


async def _sync_google_media_mirror(db: AsyncSession) -> None:
    """Mirror the user's Gemini LLM credential into a hidden ``google``
    ``media_provider_configs`` row so Veo / Nano Banana show up as
    image_gen / video_gen options without forcing the user to re-enter the
    same key in two places. Idempotent — call it after any LLM CRUD that
    touches a ``provider='gemini'`` row.

    Picks the most recently updated gemini config when the user has more
    than one (e.g. different model_name on the same key — they all share
    the API key anyway). Caller is responsible for committing.
    """
    gemini_result = await db.execute(
        select(LLMConfig)
        .where(LLMConfig.provider == "gemini")
        .order_by(LLMConfig.updated_at.desc())
        .limit(1)
    )
    gemini = gemini_result.scalar_one_or_none()

    mirror_result = await db.execute(
        select(MediaProviderConfig).where(MediaProviderConfig.provider == "google")
    )
    mirror = mirror_result.scalar_one_or_none()

    if gemini:
        creds = {"api_key": gemini.api_key}
        if mirror is None:
            db.add(MediaProviderConfig(
                provider="google",
                label=_GOOGLE_MIRROR_LABEL,
                credentials=creds,
                defaults={"aspect_ratio": "portrait"},
                is_active=True,
            ))
        else:
            mirror.credentials = creds
            mirror.is_active = True
            # Refresh label only if it still matches the auto-managed marker —
            # a human-edited label is left alone.
            if mirror.label == _GOOGLE_MIRROR_LABEL:
                mirror.label = _GOOGLE_MIRROR_LABEL
    elif mirror is not None:
        # No gemini LLM left — drop the mirror. FK cascade clears
        # MediaCapabilityDefault rows that pointed at it.
        await db.delete(mirror)


async def list_llm_configs(db: AsyncSession) -> list[LLMConfigResponse]:
    result = await db.execute(select(LLMConfig).order_by(LLMConfig.created_at))
    configs = result.scalars().all()
    return [_to_response(c) for c in configs]


async def create_llm_config(db: AsyncSession, data: LLMConfigCreate) -> LLMConfigResponse:
    # Deactivate all existing configs, new one becomes active
    all_result = await db.execute(select(LLMConfig))
    for c in all_result.scalars().all():
        c.is_active = False

    config = LLMConfig(
        label=data.label,
        provider=data.provider,
        api_key=data.api_key,
        base_url=data.base_url,
        model_name=data.model_name,
        is_active=True,
    )
    db.add(config)
    await db.flush()  # need config.id before sync
    if data.provider == "gemini":
        await _sync_google_media_mirror(db)
    await db.commit()
    await db.refresh(config)
    return _to_response(config)


async def update_llm_config(
    db: AsyncSession, config_id: uuid.UUID, data: LLMConfigUpdate
) -> LLMConfigResponse:
    result = await db.execute(select(LLMConfig).where(LLMConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="LLM config not found")

    # Capture the *old* provider before mutating — needed so we re-sync
    # the google mirror when the user flips a row away from gemini.
    was_gemini = config.provider == "gemini"

    if data.label is not None:
        config.label = data.label
    if data.provider is not None:
        config.provider = data.provider
    if data.api_key is not None:
        config.api_key = data.api_key
    if data.base_url is not None:
        config.base_url = data.base_url
    if data.model_name is not None:
        config.model_name = data.model_name

    if was_gemini or config.provider == "gemini":
        await db.flush()
        await _sync_google_media_mirror(db)
    await db.commit()
    await db.refresh(config)
    return _to_response(config)


async def delete_llm_config(db: AsyncSession, config_id: uuid.UUID) -> None:
    result = await db.execute(select(LLMConfig).where(LLMConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="LLM config not found")

    if config.is_active:
        # Auto-activate another config if one exists
        other = await db.execute(select(LLMConfig).where(LLMConfig.id != config_id).limit(1))
        next_config = other.scalar_one_or_none()
        if next_config:
            next_config.is_active = True

    was_gemini = config.provider == "gemini"
    await db.delete(config)
    if was_gemini:
        await db.flush()
        await _sync_google_media_mirror(db)
    await db.commit()


async def activate_llm_config(db: AsyncSession, config_id: uuid.UUID) -> LLMConfigResponse:
    result = await db.execute(select(LLMConfig).where(LLMConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="LLM config not found")

    # Deactivate all
    all_result = await db.execute(select(LLMConfig))
    for c in all_result.scalars().all():
        c.is_active = False

    config.is_active = True
    await db.commit()
    await db.refresh(config)
    return _to_response(config)


async def get_scene_configs(db: AsyncSession, language: str = "zh-CN") -> LLMSceneConfigsResponse:
    from app.apps.registry import AppRegistry
    from app.schemas.setting import pick_label

    # Load all existing config rows indexed by (scene_key, model_type)
    rows_result = await db.execute(select(ModelSceneConfig))
    rows: dict[tuple[str, str], ModelSceneConfig] = {
        (r.scene_key, r.model_type): r for r in rows_result.scalars().all()
    }

    # Load all LLM configs for label lookup
    configs_result = await db.execute(select(LLMConfig))
    configs_by_id: dict[str, LLMConfig] = {str(c.id): c for c in configs_result.scalars().all()}

    def _mt_label(mt: str) -> str:
        return pick_label(MODEL_TYPE_LABELS.get(mt, mt), language)

    sections: list[SceneSection] = []
    # Guard against duplicate scene_keys (would trip Alpine x-for's
    # :key uniqueness check). Caused a "Duplicate key" warning when
    # ``asset_tagging`` was registered both as a system scene and an
    # app scene — kept here even after that fix so a future addition
    # can't silently re-introduce the same bug.
    seen_keys: set[str] = set()

    # System scenes first
    for scene_key, sys_def in SYSTEM_SCENES.items():
        if scene_key in seen_keys:
            continue
        seen_keys.add(scene_key)
        model_configs = []
        for mt in sys_def["model_types"]:
            row = rows.get((scene_key, mt))
            config_id = str(row.config_id) if row and row.config_id else None
            model_configs.append(ModelTypeConfig(
                model_type=mt,
                model_type_label=_mt_label(mt),
                config_id=config_id,
                config_label=configs_by_id[config_id].label if config_id and config_id in configs_by_id else None,
                model_name=row.model_name if row else None,
            ))
        sections.append(SceneSection(
            scene_key=scene_key,
            label=pick_label(sys_def["label"], language),
            icon=sys_def["icon"],
            scene_type="system",
            model_configs=model_configs,
        ))

    # Active app scenes — localize each app's name via its registry helper
    app_lang = "en" if (language or "").lower().startswith("en") else "zh"
    for app in AppRegistry.list_apps():
        if app.status != "active":
            continue
        if app.app_id in seen_keys:
            continue
        seen_keys.add(app.app_id)
        localized_app = app.localized(app_lang)
        model_configs = []
        for mt in localized_app.required_model_types:
            row = rows.get((localized_app.app_id, mt))
            config_id = str(row.config_id) if row and row.config_id else None
            model_configs.append(ModelTypeConfig(
                model_type=mt,
                model_type_label=_mt_label(mt),
                config_id=config_id,
                config_label=configs_by_id[config_id].label if config_id and config_id in configs_by_id else None,
                model_name=row.model_name if row else None,
            ))
        sections.append(SceneSection(
            scene_key=localized_app.app_id,
            label=localized_app.name,
            icon=localized_app.icon,
            scene_type="app",
            model_configs=model_configs,
        ))

    return LLMSceneConfigsResponse(sections=sections)


async def update_scene_configs(db: AsyncSession, data: LLMSceneConfigsUpdate, language: str = "zh-CN") -> LLMSceneConfigsResponse:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    for upd in data.updates:
        config_id = uuid.UUID(upd.config_id) if upd.config_id else None
        # Clearing the endpoint also clears the model override — a row with a
        # model_name but no config_id would be orphaned.
        model_name = upd.model_name if config_id else None
        stmt = (
            pg_insert(ModelSceneConfig)
            .values(
                scene_key=upd.scene_key,
                model_type=upd.model_type,
                config_id=config_id,
                model_name=model_name,
            )
            .on_conflict_do_update(
                index_elements=["scene_key", "model_type"],
                set_={"config_id": config_id, "model_name": model_name},
            )
        )
        await db.execute(stmt)
    await db.commit()
    return await get_scene_configs(db, language=language)


# ── Media capability defaults (image / video / tts) ───────────────

# What each capability is, what providers + models support it.
# If a provider has this capability, we list its offerings here.
# Label / description values are (zh, en) tuples; callers localize via pick_label().
# Model display suffix tuples keep the (zh, en) story for ByteDance / Kuaishou / etc.
_CAPABILITY_META = {
    "image_gen": {
        "label": ("图像生成", "Image Generation"),
        "icon": "🖼️",
        "description": (
            "用于生成封面图、产品图、辅助配图",
            "For cover images, product shots, and supporting visuals",
        ),
        # (provider → list of (model_code, (zh_label, en_label)))
        "models_by_provider": {
            "chanjing": [
                ("doubao-seedream-4.5", ("Seedream 4.5 · 字节",   "Seedream 4.5 · ByteDance")),
                ("doubao-seedream-4.0", ("Seedream 4.0 · 字节",   "Seedream 4.0 · ByteDance")),
                ("doubao-seedream-3.0", ("Seedream 3.0 · 字节",   "Seedream 3.0 · ByteDance")),
                ("kling-v2-1",          ("Kling v2.1 · 快手",     "Kling v2.1 · Kuaishou")),
                ("kling-v2",            ("Kling v2 · 快手",       "Kling v2 · Kuaishou")),
                ("wan2.2-t2i",          ("Wan 2.2 · 阿里",        "Wan 2.2 · Alibaba")),
            ],
            "google": [
                ("gemini-3-pro-image-preview",    ("Nano Banana Pro · Google (推荐)",  "Nano Banana Pro · Google (recommended)")),
                ("gemini-3.1-flash-image-preview",("Nano Banana 2 · Google (快)",     "Nano Banana 2 · Google (fast)")),
                ("gemini-2.5-flash-image",        ("Nano Banana · Google (稳定)",     "Nano Banana · Google (stable)")),
            ],
        },
    },
    "video_gen": {
        "label": ("视频生成", "Video Generation"),
        "icon": "🎬",
        "description": (
            "用于 B-roll 分镜生成、图生视频",
            "For B-roll scene generation and image-to-video",
        ),
        "models_by_provider": {
            "chanjing": [
                ("Doubao-Seedance-1.0-pro",     ("Seedance 1.0 Pro · 字节 (推荐)", "Seedance 1.0 Pro · ByteDance (recommended)")),
                ("doubao-seedance-1.0-lite-i2v",("Seedance 1.0 Lite · 字节",       "Seedance 1.0 Lite · ByteDance")),
                ("tx_kling-v2-1-master",        ("Kling v2.1 Master · 快手",       "Kling v2.1 Master · Kuaishou")),
                ("kling-2.5",                   ("Kling 2.5 · 快手",               "Kling 2.5 · Kuaishou")),
                ("MiniMax-Hailuo-02",           ("Hailuo 02 · MiniMax",            "Hailuo 02 · MiniMax")),
                ("viduq1",                      ("Vidu Q1",                        "Vidu Q1")),
            ],
            "google": [
                # Gemini API as of 2026-04: only Veo 3.1 series currently available
                # (veo-3-generate-preview shut down 2026-03-09; veo-2 no longer listed)
                ("veo-3.1-generate-preview",      ("Veo 3.1 · Google (推荐)",     "Veo 3.1 · Google (recommended)")),
                ("veo-3.1-lite-generate-preview", ("Veo 3.1 Lite · Google (快)", "Veo 3.1 Lite · Google (fast)")),
            ],
        },
    },
    "tts": {
        "label": ("语音合成", "Voice Synthesis"),
        "icon": "🔊",
        "description": (
            "选择默认 TTS 供应商。供应商内部集成了多种语音引擎（Cicada、ElevenLabs 等），具体音色在生成视频时选择。",
            "Pick a default TTS provider. Each provider wraps multiple underlying engines (Cicada, ElevenLabs, …); the exact voice is chosen when you generate a video.",
        ),
        # TTS uses voice_id, not model_code. Provider transparently routes to
        # the underlying engine (Cicada / ElevenLabs / ...) based on the voice.
        "models_by_provider": {
            "chanjing": [],  # voices listed dynamically from provider API
            "jogg": [],
        },
    },
}


async def get_media_capability_configs(
    db: AsyncSession, language: str = "zh-CN"
) -> MediaCapabilitiesResponse:
    """Build the capability → options mapping based on configured media providers."""
    from app.schemas.setting import pick_label

    is_en = (language or "").lower().startswith("en")
    tts_suffix = " (TTS provider)" if is_en else "（TTS 供应商）"

    # Load active providers
    providers_result = await db.execute(
        select(MediaProviderConfig).where(MediaProviderConfig.is_active.is_(True))
    )
    active_providers = list(providers_result.scalars().all())
    providers_by_name: dict[str, list[MediaProviderConfig]] = {}
    for p in active_providers:
        providers_by_name.setdefault(p.provider, []).append(p)

    # Load current defaults
    defaults_result = await db.execute(select(MediaCapabilityDefault))
    defaults: dict[str, MediaCapabilityDefault] = {
        d.capability: d for d in defaults_result.scalars().all()
    }

    # Friendly suffix appended to ghost (unconfigured) options so the
    # dropdown communicates *why* the row is disabled at a glance.
    unconfigured_suffix = " (not configured)" if is_en else "（未配置）"

    capabilities: list[MediaCapabilityConfig] = []
    for cap, meta in _CAPABILITY_META.items():
        options: list[MediaCapabilityOption] = []
        for provider_name, models in meta["models_by_provider"].items():
            configured = providers_by_name.get(provider_name, [])
            if configured:
                for p in configured:
                    if cap == "tts":
                        # TTS is "pick provider, voice chosen per-use" — label clarifies
                        # this is a provider choice, not a model or voice selection
                        options.append(MediaCapabilityOption(
                            provider_config_id=str(p.id),
                            provider=p.provider,
                            provider_label=p.label,
                            model_code=None,
                            voice_id=None,
                            display_label=f"{p.label}{tts_suffix}",
                        ))
                    else:
                        for code, title in models:
                            options.append(MediaCapabilityOption(
                                provider_config_id=str(p.id),
                                provider=p.provider,
                                provider_label=p.label,
                                model_code=code,
                                voice_id=None,
                                display_label=pick_label(title, language),
                            ))
            else:
                # No credential row for this provider — emit ghost options so
                # users see the model exists but can't pick it. The UI must
                # render these disabled.
                if cap == "tts":
                    options.append(MediaCapabilityOption(
                        provider_config_id="",
                        provider=provider_name,
                        provider_label=provider_name,
                        model_code=None,
                        voice_id=None,
                        display_label=f"{provider_name}{tts_suffix}{unconfigured_suffix}",
                        available=False,
                    ))
                else:
                    for code, title in models:
                        options.append(MediaCapabilityOption(
                            provider_config_id="",
                            provider=provider_name,
                            provider_label=provider_name,
                            model_code=code,
                            voice_id=None,
                            display_label=pick_label(title, language) + unconfigured_suffix,
                            available=False,
                        ))

        d = defaults.get(cap)
        capabilities.append(MediaCapabilityConfig(
            capability=cap,
            label=pick_label(meta["label"], language),
            icon=meta["icon"],
            description=pick_label(meta["description"], language),
            current_provider_config_id=str(d.provider_config_id) if d and d.provider_config_id else None,
            current_model_code=d.model_code if d else None,
            current_voice_id=d.voice_id if d else None,
            options=options,
        ))
    return MediaCapabilitiesResponse(capabilities=capabilities)


async def update_media_capability_configs(
    db: AsyncSession, data: MediaCapabilitiesUpdateRequest, language: str = "zh-CN"
) -> MediaCapabilitiesResponse:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    for upd in data.updates:
        provider_id = uuid.UUID(upd.provider_config_id) if upd.provider_config_id else None
        stmt = (
            pg_insert(MediaCapabilityDefault)
            .values(
                capability=upd.capability,
                provider_config_id=provider_id,
                model_code=upd.model_code,
                voice_id=upd.voice_id,
            )
            .on_conflict_do_update(
                index_elements=["capability"],
                set_={
                    "provider_config_id": provider_id,
                    "model_code": upd.model_code,
                    "voice_id": upd.voice_id,
                },
            )
        )
        await db.execute(stmt)
    await db.commit()
    return await get_media_capability_configs(db, language=language)


async def get_llm_config_for_scene(
    db: AsyncSession, scene_key: str, model_type: str = "text_llm"
) -> LLMConfig | None:
    """Return the scene-bound LLM config with the scene's model override applied.

    Two kinds of user choice stack here:
      1. endpoint choice — `model_scene_configs.config_id` → picks url+key+default
      2. model choice    — `model_scene_configs.model_name` → overrides which
         model on that endpoint to invoke (for aggregator proxies serving
         many models under one key)

    A scene override is an *explicit* user choice — honor it even when the
    target config's `is_active` flag is False. `is_active` is reserved for
    "which config is the system-wide fallback", not "which configs are
    usable at all".

    The returned LLMConfig has its in-memory `model_name` attribute mutated to
    reflect the scene's model override when set; callers downstream (adapter
    factory) use `cfg.model_name` as-is.
    """
    import logging
    _log = logging.getLogger(__name__)

    result = await db.execute(
        select(ModelSceneConfig).where(
            ModelSceneConfig.scene_key == scene_key,
            ModelSceneConfig.model_type == model_type,
        )
    )
    row = result.scalar_one_or_none()
    if not row or not row.config_id:
        return None
    config_result = await db.execute(
        select(LLMConfig).where(LLMConfig.id == row.config_id)
    )
    cfg = config_result.scalar_one_or_none()
    if cfg is None:
        return None
    # Apply scene-level model override (in-memory only; do not flush back).
    if row.model_name:
        cfg.model_name = row.model_name
    if not cfg.is_active:
        _log.info(
            "Scene override in use but underlying config is not flagged is_active: "
            "scene=%s model_type=%s config=%s/%s — honoring explicit scene override anyway.",
            scene_key, model_type, cfg.provider, cfg.model_name,
        )
    return cfg


async def get_active_llm_config(db: AsyncSession) -> LLMConfig | None:
    result = await db.execute(select(LLMConfig).where(LLMConfig.is_active == True))  # noqa: E712
    return result.scalar_one_or_none()


# In-memory cache for per-endpoint model lists.
# Key: str(config_id); Value: (models list, timestamp).
# TTL of 5 minutes balances "pick up new models fairly quickly" against
# "don't hammer /v1/models on every settings page load".
_endpoint_models_cache: dict[str, tuple[list[str], float]] = {}
_ENDPOINT_MODELS_TTL = 300.0  # seconds


async def get_endpoint_models(db: AsyncSession, config_id: str) -> list[str]:
    """Return available models for a saved endpoint.

    5-minute TTL cache. On upstream failure falls back to the stale cache
    (last-known-good) or, as final fallback, [cfg.model_name] so the UI
    always has at least the endpoint's default to show.
    """
    import time
    import uuid as _uuid
    cache_key = str(config_id)
    now = time.time()
    cached = _endpoint_models_cache.get(cache_key)
    if cached and (now - cached[1] < _ENDPOINT_MODELS_TTL):
        return cached[0]

    cfg = await db.get(LLMConfig, _uuid.UUID(cache_key))
    if cfg is None:
        raise HTTPException(status_code=404, detail="LLM config not found")

    try:
        models, _rec = await fetch_llm_models(cfg.api_key, cfg.base_url, cfg.provider)
        _endpoint_models_cache[cache_key] = (models, now)
        return models
    except Exception:
        if cached:
            return cached[0]
        if cfg.model_name:
            return [cfg.model_name]
        raise


def _pick_recommended(model_ids: list[str], provider: str) -> str:
    if not model_ids:
        return ""
    if provider == "openai":
        for m in model_ids:
            if m == "gpt-4o":
                return m
    elif provider == "anthropic":
        for m in model_ids:
            if "claude-opus" in m:
                return m
        for m in model_ids:
            if "claude-sonnet" in m:
                return m
    elif provider == "gemini":
        for preferred in ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"):
            for m in model_ids:
                if m.startswith(preferred):
                    return m
    elif provider == "minimax":
        for m in model_ids:
            if m == "MiniMax-M2.7":
                return m
    elif provider == "deepseek":
        for m in model_ids:
            if m == "deepseek-chat":
                return m
    elif provider == "kimi":
        for preferred in ("kimi-k2.6", "kimi-k2.5", "moonshot-v1-32k"):
            if preferred in model_ids:
                return preferred
    elif provider == "grok":
        for preferred in ("grok-4-latest", "grok-4-0709", "grok-3", "grok-3-mini"):
            if preferred in model_ids:
                return preferred
    elif provider == "ollama":
        for preferred in ("llama3.2:latest", "llama3:latest", "qwen2.5:latest", "mistral:latest"):
            if preferred in model_ids:
                return preferred
    return model_ids[0]


# MiniMax has no /models endpoint — always return this static list.
# Anthropic is used only as a *fallback* when /models fails (common for
# aggregator proxies that forward Anthropic-format requests but do not implement
# the /models introspection endpoint).
_STATIC_MODELS: dict[str, list[str]] = {
    "minimax": [
        "MiniMax-M2.7",
        "MiniMax-M2.7-highspeed",
        "MiniMax-M2.5",
        "MiniMax-M2.5-highspeed",
        "MiniMax-M2.1",
        "MiniMax-M2.1-highspeed",
        "MiniMax-M2",
    ],
    "anthropic": [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-opus-4-5-20251101",
        "claude-opus-4-1-20250805",
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-20250514",
        "claude-haiku-4-5-20251001",
    ],
}

# Known non-chat model families — these always get filtered out from any
# OpenAI-compatible /v1/models response (embeddings, audio, image-gen, etc.)
# Kept as a block-list rather than a whitelist so that aggregator proxies can
# expose Claude / Gemini / Qwen / DeepSeek / Llama / etc. under an OpenAI or
# Anthropic-flavored endpoint without being silently hidden.
_NON_CHAT_MODEL_PREFIXES: tuple[str, ...] = (
    "text-embedding-",
    "text-similarity-",
    "text-search-",
    "text-moderation-",
    "whisper-",
    "tts-",
    "dall-e-",
    "babbage-",
    "davinci-",
    "code-",
    "omni-moderation-",
)


async def fetch_llm_models(api_key: str, base_url: str, provider: str) -> tuple[list[str], str]:
    """Returns (model_ids, recommended_id). Raises HTTPException on failure.

    Design note: aggregator proxies (OneAPI / NewAPI / LiteLLM / enterprise
    OpenAI-compatible gateways) commonly serve Claude / Gemini / Qwen / DeepSeek
    / Llama under a single endpoint. We therefore do NOT hard-filter by model
    family prefix — instead we remove known non-chat types (embedding / TTS /
    whisper / image-gen) and return everything else. Users connected to real
    OpenAI still get a clean chat-only list; users on aggregators see the full
    catalogue.
    """
    try:
        # MiniMax has no introspection endpoint — always return static list.
        if provider == "minimax":
            model_ids = _STATIC_MODELS["minimax"]
            return model_ids, _pick_recommended(model_ids, provider)

        if provider == "anthropic":
            import httpx
            # Try /models first so that aggregators returning their actual
            # inventory are respected. Fall back to the static Claude list only
            # if the endpoint is missing or unreachable.
            try:
                url = f"{base_url.rstrip('/')}/models"
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        url,
                        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                        timeout=15,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    model_ids = [m["id"] for m in data.get("data", [])]
                    if not model_ids:
                        raise ValueError("empty model list")
            except Exception:
                model_ids = _STATIC_MODELS["anthropic"]
        elif provider == "gemini":
            # Gemini's OpenAI-compatibility layer does not expose /models list.
            # Use Gemini's native /v1beta/models endpoint instead — reconstruct
            # it from whatever base_url the user gave (handles both
            # https://generativelanguage.googleapis.com/v1beta and .../v1beta/openai).
            import httpx
            base = base_url.rstrip('/')
            if base.endswith('/openai'):
                base = base[:-len('/openai')]
            if not base.endswith('/v1beta'):
                base = "https://generativelanguage.googleapis.com/v1beta"
            url = f"{base}/models"
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url,
                    params={"key": api_key},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                # Response: {"models": [{"name": "models/gemini-2.0-flash", "supportedGenerationMethods": [...], ...}]}
                all_models = data.get("models", [])
                # Filter to models that support text generation via generateContent
                chat_models = [
                    m for m in all_models
                    if "generateContent" in (m.get("supportedGenerationMethods") or [])
                ]
                model_ids = [m["name"].removeprefix("models/") for m in chat_models]
        else:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            result = await client.models.list()
            model_ids = [m.id for m in result.data]

            # Block-list filter (not family whitelist) — see _NON_CHAT_MODEL_PREFIXES.
            # Keeps real OpenAI dropdown clean while letting aggregator proxies
            # expose their full chat-capable catalogue (GPT + Claude + Gemini + ...).
            model_ids = [
                m for m in model_ids
                if not any(m.startswith(p) for p in _NON_CHAT_MODEL_PREFIXES)
            ]

        if not model_ids:
            raise HTTPException(status_code=422, detail="No available models found")

        recommended = _pick_recommended(model_ids, provider)
        return model_ids, recommended
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch model list: {e}") from e


async def validate_llm_connection(api_key: str, base_url: str, model_name: str, provider: str = "custom") -> None:
    """Validates LLM connection. Raises HTTPException with detail on failure."""
    try:
        if provider == "anthropic":
            import httpx
            url = f"{base_url.rstrip('/')}/v1/messages"
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model_name,
                        "max_tokens": 5,
                        "messages": [{"role": "user", "content": "Hi"}],
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("content"):
                    raise HTTPException(status_code=400, detail="Empty response from API, please check the model name")
        else:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            response = await client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            )
            if not response.choices:
                raise HTTPException(status_code=400, detail="Empty response from API, please check the model name")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("validate_llm_connection failed [provider=%s base_url=%s model=%s]: %s", provider, base_url, model_name, e)
        raise HTTPException(status_code=400, detail=str(e)) from e
