from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.context_service import ContextService
from app.application.memory_service import list_memories_for_offer, render_memories_block


def _model_dump(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    data: dict[str, Any] = {}
    for key in (
        "id",
        "name",
        "title",
        "description",
        "knowledge_type",
        "content_raw",
        "asset_type",
        "tags_json",
        "parse_status",
        "metadata_json",
        "preview_uri",
    ):
        if hasattr(obj, key):
            value = getattr(obj, key)
            data[key] = str(value) if key == "id" and value is not None else value
    return data


async def build_marketing_context_pack(
    db: AsyncSession,
    *,
    offer_id: uuid.UUID,
    surface: str = "all",
    intent: str | None = None,
    language: str = "zh-CN",
    max_knowledge: int = 20,
    max_assets: int = 20,
) -> dict[str, Any]:
    """Return a compact, reusable context pack for agents and app runtimes.

    This is not a new source of truth. It wraps the existing ContextService and
    MemoryService into one stable shape so WebUI and MCP can pull the same
    grounding bundle instead of rediscovering separate endpoints.
    """
    context_svc = ContextService(db)
    context = await context_svc.get_offer_context(offer_id)

    memory_surface = surface if surface in {"image", "script"} else None
    memories = await list_memories_for_offer(
        db,
        offer_id=offer_id,
        surface=memory_surface,
        active_only=True,
    )
    brand_voice = await context_svc.resolve_brand_voice(offer_id)
    lang = "en" if language.startswith("en") else "zh"

    knowledge_items = [
        _model_dump(item)
        for item in context.knowledge_items[: max(0, max_knowledge)]
    ]
    assets = [
        _model_dump(asset)
        for asset in context.assets[: max(0, max_assets)]
    ]
    memory_items = [
        {
            "id": str(memory.id),
            "scope_type": memory.scope_type,
            "scope_id": str(memory.scope_id),
            "surface": memory.surface,
            "content": memory.content,
            "source": memory.source,
        }
        for memory in memories
    ]

    return {
        "pack_type": "marketing_context",
        "version": 1,
        "offer_id": str(offer_id),
        "merchant_id": str(context.offer.merchant_id),
        "surface": surface,
        "intent": intent,
        "offer": _model_dump(context.offer),
        "merchant": _model_dump(context.merchant),
        "summaries": {
            "merchant_knowledge": context.merchant_knowledge.model_dump(mode="json"),
            "offer_knowledge": context.offer_knowledge.model_dump(mode="json"),
            "merchant_assets": context.merchant_assets.model_dump(mode="json"),
            "offer_assets": context.offer_assets.model_dump(mode="json"),
            "available_proof_assets": context.available_proof_assets,
            "selling_points": context.selling_points,
            "target_audiences": context.target_audiences,
            "target_scenarios": context.target_scenarios,
        },
        "knowledge_items": knowledge_items,
        "assets": assets,
        "brand_voice": brand_voice,
        "memories": memory_items,
        "prompt_blocks": {
            "brand_voice": brand_voice or "",
            "memories": render_memories_block(memories, lang=lang),
        },
    }
