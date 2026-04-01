from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException

logger = logging.getLogger(__name__)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_config import LLMConfig
from app.models.model_scene_config import ModelSceneConfig
from app.schemas.setting import (
    LLMConfigCreate,
    LLMConfigResponse,
    LLMConfigUpdate,
    LLMSceneConfigsResponse,
    LLMSceneConfigsUpdate,
    MODEL_TYPE_LABELS,
    ModelTypeConfig,
    SceneSection,
    SYSTEM_SCENES,
)


def _mask_key(api_key: str) -> str:
    if len(api_key) <= 4:
        return "••••"
    return "••••••••" + api_key[-4:]


def _to_response(config: LLMConfig) -> LLMConfigResponse:
    return LLMConfigResponse(
        id=str(config.id),
        label=config.label,
        provider=config.provider,
        api_key_masked=_mask_key(config.api_key),
        base_url=config.base_url,
        model_name=config.model_name,
        is_active=config.is_active,
    )


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

    await db.delete(config)
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


async def get_scene_configs(db: AsyncSession) -> LLMSceneConfigsResponse:
    from app.apps.registry import AppRegistry

    # Load all existing config rows indexed by (scene_key, model_type)
    rows_result = await db.execute(select(ModelSceneConfig))
    rows: dict[tuple[str, str], ModelSceneConfig] = {
        (r.scene_key, r.model_type): r for r in rows_result.scalars().all()
    }

    # Load all LLM configs for label lookup
    configs_result = await db.execute(select(LLMConfig))
    configs_by_id: dict[str, LLMConfig] = {str(c.id): c for c in configs_result.scalars().all()}

    sections: list[SceneSection] = []

    # System scenes first
    for scene_key, sys_def in SYSTEM_SCENES.items():
        model_configs = []
        for mt in sys_def["model_types"]:
            row = rows.get((scene_key, mt))
            config_id = str(row.config_id) if row and row.config_id else None
            model_configs.append(ModelTypeConfig(
                model_type=mt,
                model_type_label=MODEL_TYPE_LABELS.get(mt, mt),
                config_id=config_id,
                config_label=configs_by_id[config_id].label if config_id and config_id in configs_by_id else None,
            ))
        sections.append(SceneSection(
            scene_key=scene_key,
            label=sys_def["label"],
            icon=sys_def["icon"],
            scene_type="system",
            model_configs=model_configs,
        ))

    # Active app scenes
    for app in AppRegistry.list_apps():
        if app.status != "active":
            continue
        model_configs = []
        for mt in app.required_model_types:
            row = rows.get((app.app_id, mt))
            config_id = str(row.config_id) if row and row.config_id else None
            model_configs.append(ModelTypeConfig(
                model_type=mt,
                model_type_label=MODEL_TYPE_LABELS.get(mt, mt),
                config_id=config_id,
                config_label=configs_by_id[config_id].label if config_id and config_id in configs_by_id else None,
            ))
        sections.append(SceneSection(
            scene_key=app.app_id,
            label=app.name,
            icon=app.icon,
            scene_type="app",
            model_configs=model_configs,
        ))

    return LLMSceneConfigsResponse(sections=sections)


async def update_scene_configs(db: AsyncSession, data: LLMSceneConfigsUpdate) -> LLMSceneConfigsResponse:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    for upd in data.updates:
        config_id = uuid.UUID(upd.config_id) if upd.config_id else None
        stmt = (
            pg_insert(ModelSceneConfig)
            .values(scene_key=upd.scene_key, model_type=upd.model_type, config_id=config_id)
            .on_conflict_do_update(
                index_elements=["scene_key", "model_type"],
                set_={"config_id": config_id},
            )
        )
        await db.execute(stmt)
    await db.commit()
    return await get_scene_configs(db)


async def get_llm_config_for_scene(
    db: AsyncSession, scene_key: str, model_type: str = "text_llm"
) -> LLMConfig | None:
    result = await db.execute(
        select(ModelSceneConfig).where(
            ModelSceneConfig.scene_key == scene_key,
            ModelSceneConfig.model_type == model_type,
        )
    )
    row = result.scalar_one_or_none()
    if not row or not row.config_id:
        return None
    config_result = await db.execute(select(LLMConfig).where(LLMConfig.id == row.config_id))
    return config_result.scalar_one_or_none()


async def get_active_llm_config(db: AsyncSession) -> LLMConfig | None:
    result = await db.execute(select(LLMConfig).where(LLMConfig.is_active == True))  # noqa: E712
    return result.scalar_one_or_none()


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
    elif provider == "ollama":
        for preferred in ("llama3.2:latest", "llama3:latest", "qwen2.5:latest", "mistral:latest"):
            if preferred in model_ids:
                return preferred
    return model_ids[0]


# Static model lists for providers without a /models endpoint
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
}


async def fetch_llm_models(api_key: str, base_url: str, provider: str) -> tuple[list[str], str]:
    """Returns (model_ids, recommended_id). Raises HTTPException on failure."""
    try:
        # Providers with no /models endpoint — return static list
        if provider in _STATIC_MODELS:
            model_ids = _STATIC_MODELS[provider]
            return model_ids, _pick_recommended(model_ids, provider)

        if provider == "anthropic":
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                model_ids = [m["id"] for m in data.get("data", [])]
        else:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            result = await client.models.list()
            model_ids = [m.id for m in result.data]

            if provider == "openai":
                CHAT_PREFIXES = ("gpt-", "o1", "o3", "chatgpt-")
                model_ids = [m for m in model_ids if any(m.startswith(p) for p in CHAT_PREFIXES)]

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
    from openai import AsyncOpenAI

    extra_headers = {}
    if provider == "anthropic":
        extra_headers["anthropic-version"] = "2023-06-01"

    client = AsyncOpenAI(api_key=api_key, base_url=base_url, default_headers=extra_headers)
    try:
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
