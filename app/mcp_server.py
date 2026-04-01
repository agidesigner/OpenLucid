"""
OpenLucid MCP Server

Exposes core platform capabilities as MCP tools for AI agents.
Run with: python -m app.mcp_server
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.config import settings
from app.database import async_session_factory

mcp = FastMCP(
    "OpenLucid",
    instructions=(
        "OpenLucid is an AI Director Platform for merchants. "
        "Use these tools to manage merchants, offers, knowledge, assets, brand kits, generate topic plans, "
        "and manage strategy units. A StrategyUnit represents a specific audience × scenario × "
        "objective × channel combination under an offer — the core unit of content production strategy. "
        "Knowledge Base is the offer-level total cognition layer (stable facts). Strategy Units reference "
        "knowledge items and assets via link tables (many-to-many) with role, priority, and note metadata. "
        "Brand Kit (品牌规范) defines style profiles, persona, visual guidelines for a merchant or offer scope. "
        "Start by listing merchants, then browse their offers, knowledge, brand kits, and assets."
    ),
)

# Module-level session factory reference; tests can monkey-patch this.
_session_factory = async_session_factory


def _serialize(obj: Any, schema_cls: type | None = None) -> str:
    """Serialize an object to JSON string.

    If schema_cls is provided, validates the object through a Pydantic schema
    with from_attributes=True (useful for SQLAlchemy models).
    """
    if schema_cls is not None:
        model = schema_cls.model_validate(obj, from_attributes=True)
        return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if hasattr(obj, "model_dump"):
        return json.dumps(obj.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if isinstance(obj, list):
        items = []
        for item in obj:
            if hasattr(item, "model_dump"):
                items.append(item.model_dump(mode="json"))
            else:
                items.append(item)
        return json.dumps(items, ensure_ascii=False, indent=2, default=str)
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


# ── Merchant Tools ──────────────────────────────────────────────


@mcp.tool()
async def create_merchant(
    name: str,
    merchant_type: str = "goods",
    default_locale: str = "zh-CN",
) -> str:
    """Create a new merchant. merchant_type: goods | service | hybrid."""
    from app.application.merchant_service import MerchantService
    from app.schemas.merchant import MerchantCreate, MerchantResponse

    async with _session_factory() as session:
        svc = MerchantService(session)
        data = MerchantCreate(
            name=name,
            merchant_type=merchant_type,
            default_locale=default_locale,
        )
        merchant = await svc.create(data)
        await session.commit()
        return _serialize(merchant, MerchantResponse)


@mcp.tool()
async def list_merchants(page: int = 1, page_size: int = 20) -> str:
    """List all merchants with pagination."""
    from app.application.merchant_service import MerchantService
    from app.schemas.merchant import MerchantResponse

    async with _session_factory() as session:
        svc = MerchantService(session)
        items, total = await svc.list(page=page, page_size=page_size)
        serialized_items = [MerchantResponse.model_validate(i, from_attributes=True).model_dump(mode="json") for i in items]
        return json.dumps({"total": total, "page": page, "items": serialized_items}, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def get_merchant(merchant_id: str) -> str:
    """Get a single merchant by ID, including brand/tone/compliance profiles."""
    from app.application.merchant_service import MerchantService
    from app.schemas.merchant import MerchantResponse

    async with _session_factory() as session:
        svc = MerchantService(session)
        merchant = await svc.get(uuid.UUID(merchant_id))
        return _serialize(merchant, MerchantResponse)


# ── Offer Tools ─────────────────────────────────────────────────


@mcp.tool()
async def create_offer(
    merchant_id: str,
    name: str,
    offer_type: str = "product",
    description: str = "",
    positioning: str = "",
    core_selling_points: list[str] | None = None,
    target_audiences: list[str] | None = None,
    target_scenarios: list[str] | None = None,
    locale: str = "zh-CN",
) -> str:
    """Create an offer (product/service) under a merchant.
    offer_type: product | service | bundle | solution."""
    from app.application.offer_service import OfferService
    from app.schemas.offer import OfferCreate, OfferResponse

    async with _session_factory() as session:
        svc = OfferService(session)
        data = OfferCreate(
            merchant_id=uuid.UUID(merchant_id),
            name=name,
            offer_type=offer_type,
            description=description or None,
            positioning=positioning or None,
            core_selling_points_json={"points": core_selling_points} if core_selling_points else None,
            target_audience_json={"items": target_audiences} if target_audiences else None,
            target_scenarios_json={"items": target_scenarios} if target_scenarios else None,
            locale=locale,
        )
        offer = await svc.create(data)
        await session.commit()
        return _serialize(offer, OfferResponse)


@mcp.tool()
async def list_offers(
    merchant_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List offers with pagination. Optionally filter by merchant_id."""
    from app.application.offer_service import OfferService
    from app.schemas.offer import OfferResponse

    async with _session_factory() as session:
        svc = OfferService(session)
        mid = uuid.UUID(merchant_id) if merchant_id else None
        items, total = await svc.list(merchant_id=mid, page=page, page_size=page_size)
        serialized_items = [OfferResponse.model_validate(i, from_attributes=True).model_dump(mode="json") for i in items]
        return json.dumps({"total": total, "page": page, "items": serialized_items}, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def get_offer(offer_id: str) -> str:
    """Get a single offer by ID, including description, positioning, selling points, audiences, scenarios."""
    from app.application.offer_service import OfferService
    from app.schemas.offer import OfferResponse

    async with _session_factory() as session:
        svc = OfferService(session)
        offer = await svc.get(uuid.UUID(offer_id))
        return _serialize(offer, OfferResponse)


@mcp.tool()
async def get_brandkit(
    scope_type: str,
    scope_id: str,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List brand kits for a scope. scope_type: merchant | offer.
    Returns style profiles, persona, visual guidelines (do/don't), reference prompts."""
    from app.application.brandkit_service import BrandKitService
    from app.schemas.brandkit import BrandKitResponse

    async with _session_factory() as session:
        svc = BrandKitService(session)
        items, total = await svc.list(
            scope_type=scope_type,
            scope_id=uuid.UUID(scope_id),
            page=page,
            page_size=page_size,
        )
        serialized_items = [BrandKitResponse.model_validate(i, from_attributes=True).model_dump(mode="json") for i in items]
        return json.dumps({"total": total, "page": page, "items": serialized_items}, ensure_ascii=False, indent=2, default=str)


# ── Knowledge Tools ─────────────────────────────────────────────


@mcp.tool()
async def add_knowledge_item(
    scope_type: str,
    scope_id: str,
    title: str,
    content: str = "",
    knowledge_type: str = "general",
    language: str = "zh-CN",
) -> str:
    """Add a knowledge item to a merchant or offer.
    scope_type: merchant | offer.
    knowledge_type: brand | audience | scenario | selling_point | objection | proof | faq | general."""
    from app.application.knowledge_service import KnowledgeService
    from app.schemas.knowledge import KnowledgeItemCreate, KnowledgeItemResponse

    async with _session_factory() as session:
        svc = KnowledgeService(session)
        data = KnowledgeItemCreate(
            scope_type=scope_type,
            scope_id=uuid.UUID(scope_id),
            title=title,
            content_raw=content or None,
            knowledge_type=knowledge_type,
            language=language,
        )
        item = await svc.create(data)
        await session.commit()
        return _serialize(item, KnowledgeItemResponse)


@mcp.tool()
async def list_knowledge(
    scope_type: str,
    scope_id: str,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List knowledge items for a merchant or offer. scope_type: merchant | offer."""
    from app.application.knowledge_service import KnowledgeService
    from app.schemas.knowledge import KnowledgeItemResponse

    async with _session_factory() as session:
        svc = KnowledgeService(session)
        items, total = await svc.list(
            scope_type=scope_type,
            scope_id=uuid.UUID(scope_id),
            page=page,
            page_size=page_size,
        )
        serialized_items = [KnowledgeItemResponse.model_validate(i, from_attributes=True).model_dump(mode="json") for i in items]
        return json.dumps({"total": total, "page": page, "items": serialized_items}, ensure_ascii=False, indent=2, default=str)


# ── Asset Tools ─────────────────────────────────────────────────


@mcp.tool()
async def search_assets(
    scope_type: str,
    scope_id: str,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """Search/list assets for a merchant or offer. scope_type: merchant | offer.
    Each item includes a preview_url for viewing the file."""
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService
    from app.schemas.asset import AssetResponse

    async with _session_factory() as session:
        storage = LocalStorageAdapter()
        svc = AssetService(session, storage)
        items, total = await svc.list(
            scope_type=scope_type,
            scope_id=uuid.UUID(scope_id),
            page=page,
            page_size=page_size,
        )
        serialized_items = []
        for i in items:
            d = AssetResponse.model_validate(i, from_attributes=True).model_dump(mode="json")
            d["preview_url"] = f"/api/v1/assets/{d['id']}/file"
            serialized_items.append(d)
        return json.dumps({"total": total, "page": page, "items": serialized_items}, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def list_asset_slices(asset_id: str) -> str:
    """List all parsed slices/segments for an asset."""
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService

    async with _session_factory() as session:
        storage = LocalStorageAdapter()
        svc = AssetService(session, storage)
        slices = await svc.get_slices(uuid.UUID(asset_id))
        return _serialize(slices)


# ── Context & Topic Tools ──────────────────────────────────────


@mcp.tool()
async def get_offer_context_summary(offer_id: str) -> str:
    """Get aggregated context for an offer: merchant info, knowledge, assets, selling points, audiences.
    This is the foundation for topic plan generation."""
    from app.application.context_service import ContextService

    async with _session_factory() as session:
        svc = ContextService(session)
        ctx = await svc.get_offer_context(uuid.UUID(offer_id))
        return _serialize(ctx)


@mcp.tool()
async def generate_topic_plans(
    offer_id: str,
    count: int = 5,
    channel: str = "general",
    language: str = "zh-CN",
) -> str:
    """Generate structured topic/content plans for an offer based on its knowledge and assets.
    Plans include title, angle, hook, key points, target audience, and relevance scores.
    channel: douyin | xiaohongshu | kuaishou | video_account | general."""
    from app.application.topic_plan_service import TopicPlanService
    from app.schemas.topic_plan import TopicPlanGenerateRequest, TopicPlanResponse

    async with _session_factory() as session:
        svc = TopicPlanService(session)
        request = TopicPlanGenerateRequest(
            offer_id=uuid.UUID(offer_id),
            count=count,
            channel=channel,
            language=language,
        )
        plans = await svc.generate(request)
        await session.commit()
        serialized = [TopicPlanResponse.model_validate(p, from_attributes=True).model_dump(mode="json") for p in plans]
        return json.dumps(serialized, ensure_ascii=False, indent=2, default=str)


# ── Strategy Unit Tools ───────────────────────────────────────


@mcp.tool()
async def create_strategy_unit(
    merchant_id: str,
    offer_id: str,
    name: str,
    audience_segment: str = "",
    scenario: str = "",
    marketing_objective: str = "",
    channel: str = "",
    language: str = "zh-CN",
) -> str:
    """Create a strategy unit under an offer.
    A strategy unit represents a specific audience × scenario × objective × channel combination.
    marketing_objective: awareness | conversion | lead_generation | education | trust_building | retention | launch | branding."""
    from app.application.strategy_unit_service import StrategyUnitService
    from app.schemas.strategy_unit import StrategyUnitCreate, StrategyUnitResponse

    async with _session_factory() as session:
        svc = StrategyUnitService(session)
        data = StrategyUnitCreate(
            merchant_id=uuid.UUID(merchant_id),
            offer_id=uuid.UUID(offer_id),
            name=name,
            audience_segment=audience_segment or None,
            scenario=scenario or None,
            marketing_objective=marketing_objective or None,
            channel=channel or None,
            language=language,
        )
        unit = await svc.create(data)
        await session.commit()
        return _serialize(unit, StrategyUnitResponse)


@mcp.tool()
async def list_strategy_units(
    offer_id: str,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List strategy units for an offer."""
    from app.application.strategy_unit_service import StrategyUnitService
    from app.schemas.strategy_unit import StrategyUnitResponse

    async with _session_factory() as session:
        svc = StrategyUnitService(session)
        items, total = await svc.list(offer_id=uuid.UUID(offer_id), page=page, page_size=page_size)
        serialized_items = [StrategyUnitResponse.model_validate(i, from_attributes=True).model_dump(mode="json") for i in items]
        return json.dumps({"total": total, "page": page, "items": serialized_items}, ensure_ascii=False, indent=2, default=str)


# ── Strategy Unit Link Tools ──────────────────────────────────


@mcp.tool()
async def link_knowledge_to_strategy_unit(
    strategy_unit_id: str,
    knowledge_item_id: str,
    role: str = "general",
    priority: int = 0,
    note: str = "",
) -> str:
    """Link a knowledge item to a strategy unit (many-to-many).
    role: core_message | proof | audience_insight | scenario_anchor | objection | compliance_note | general."""
    from app.application.strategy_unit_link_service import StrategyUnitKnowledgeLinkService
    from app.schemas.strategy_unit_link import KnowledgeLinkCreate, KnowledgeLinkResponse

    async with _session_factory() as session:
        svc = StrategyUnitKnowledgeLinkService(session)
        data = KnowledgeLinkCreate(
            knowledge_item_id=uuid.UUID(knowledge_item_id),
            role=role,
            priority=priority,
            note=note or None,
        )
        link = await svc.create(uuid.UUID(strategy_unit_id), data)
        await session.commit()
        return _serialize(link, KnowledgeLinkResponse)


@mcp.tool()
async def link_asset_to_strategy_unit(
    strategy_unit_id: str,
    asset_id: str,
    role: str = "general",
    priority: int = 0,
    note: str = "",
) -> str:
    """Link an asset to a strategy unit (many-to-many).
    role: hook_asset | proof_asset | trust_asset | explainer_asset | cta_asset | general."""
    from app.application.strategy_unit_link_service import StrategyUnitAssetLinkService
    from app.schemas.strategy_unit_link import AssetLinkCreate, AssetLinkResponse

    async with _session_factory() as session:
        svc = StrategyUnitAssetLinkService(session)
        data = AssetLinkCreate(
            asset_id=uuid.UUID(asset_id),
            role=role,
            priority=priority,
            note=note or None,
        )
        link = await svc.create(uuid.UUID(strategy_unit_id), data)
        await session.commit()
        return _serialize(link, AssetLinkResponse)


if __name__ == "__main__":
    mcp.run()
