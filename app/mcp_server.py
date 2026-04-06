"""
OpenLucid MCP Server

Exposes core platform capabilities as MCP tools for AI agents.
Run with: python -m app.mcp_server
"""
from __future__ import annotations

import functools
import json
import logging
import time
import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.config import settings
from app.database import async_session_factory


def _build_transport_security() -> TransportSecuritySettings:
    """Build transport security from MCP_ALLOWED_HOSTS env var.
    Default: localhost only. Set MCP_ALLOWED_HOSTS=*.example.com to add domains."""
    import os
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost", "[::1]"]
    origins = [
        "http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*",
        "http://127.0.0.1", "http://localhost", "http://[::1]",
    ]
    extra = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    if extra:
        for domain in extra.split(","):
            d = domain.strip()
            if d:
                hosts.extend([f"{d}:*", d])
                origins.extend([f"https://{d}:*", f"https://{d}", f"http://{d}:*", f"http://{d}"])
    return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)


mcp = FastMCP(
    "OpenLucid",
    transport_security=_build_transport_security(),
    instructions=(
        "OpenLucid — AI content platform for merchants. "
        "Data model: Merchant → Offer → Knowledge / Assets / BrandKit / StrategyUnit.\n"
        "Workflow: get_merchant_overview → get_offer_context_summary → run_app (kb_qa/script_writer/topic_studio).\n"
        "Use prompts 'onboard_merchant' or 'content_brief' for guided workflows. "
        "Attach merchant:// or offer:// resources for persistent context."
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


logger = logging.getLogger("mcp.audit")


def _patch_mcp_tools():
    """Wrap all registered MCP tools with audit logging."""
    original_tool = mcp.tool

    @functools.wraps(original_tool)
    def logged_tool(*deco_args, **deco_kwargs):
        decorator = original_tool(*deco_args, **deco_kwargs)

        def wrapper(fn):
            @functools.wraps(fn)
            async def instrumented(**kwargs):
                tool_name = fn.__name__
                # Build compact args summary (skip empty/default values)
                args_summary = {k: v for k, v in kwargs.items()
                                if v is not None and v != "" and k not in ("page", "page_size")}
                t0 = time.monotonic()
                try:
                    result = await fn(**kwargs)
                    elapsed = time.monotonic() - t0
                    # Extract result count if available
                    count = ""
                    try:
                        parsed = json.loads(result)
                        if isinstance(parsed, dict) and "total" in parsed:
                            count = f" results={parsed['total']}"
                        elif isinstance(parsed, dict) and "error" in parsed:
                            count = f" error={parsed['error'][:80]}"
                    except Exception:
                        pass
                    logger.info("tool=%s args=%s%s duration=%.2fs",
                                tool_name, args_summary, count, elapsed)
                    return result
                except Exception as e:
                    elapsed = time.monotonic() - t0
                    logger.warning("tool=%s args=%s error=%s duration=%.2fs",
                                   tool_name, args_summary, e, elapsed)
                    raise

            return decorator(instrumented)
        return wrapper

    mcp.tool = logged_tool


_patch_mcp_tools()


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
    q: str = "",
    tags: str = "",
    asset_type: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """Search assets for a merchant or offer. scope_type: merchant | offer.
    q: fuzzy search across filename, title, and tag values (e.g. q='logo' matches '品牌Logo').
    tags: exact tag value match, comma-separated (e.g. tags='品牌标识,宣传物料').
    asset_type: filter by type (e.g. 'image', 'video', 'document').
    Tip: use q for keyword search, tags for precise filtering.
    Each item includes a preview_url for viewing/downloading the file."""
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService
    from app.schemas.asset import AssetResponse

    async with _session_factory() as session:
        storage = LocalStorageAdapter()
        svc = AssetService(session, storage)
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        items, total = await svc.search(
            q=q or None,
            tags=tag_list,
            asset_type=asset_type or None,
            scope_type=scope_type,
            scope_id=uuid.UUID(scope_id),
            page=page,
            page_size=page_size,
        )
        serialized_items = []
        base = settings.APP_URL.rstrip("/")
        for i in items:
            d = AssetResponse.model_validate(i, from_attributes=True).model_dump(mode="json")
            d["preview_url"] = f"{base}/api/v1/assets/{d['id']}/file"
            d.pop("preview_uri", None)  # remove ambiguous null field
            d.pop("storage_uri", None)  # internal path, not for agents
            serialized_items.append(d)
        return json.dumps({"total": total, "page": page, "items": serialized_items}, ensure_ascii=False, indent=2, default=str)


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


# ── App Tools ────────────────────────────────────────────────


@mcp.tool()
async def list_apps(language: str = "en") -> str:
    """List all available OpenLucid apps and their capabilities.
    Each app has: app_id, name, description, category, task_type,
    required_entities, required_capabilities, entry_modes, status.
    Use run_app to invoke an app's capability."""
    from app.apps.registry import AppRegistry

    apps = AppRegistry.list_apps()
    result = []
    for app in apps:
        a = app.localized(language[:2])
        result.append({
            "app_id": a.app_id,
            "name": a.name,
            "slug": a.slug,
            "description": a.description,
            "icon": a.icon,
            "category": a.category,
            "task_type": a.task_type,
            "required_entities": a.required_entities,
            "required_capabilities": a.required_capabilities,
            "entry_modes": a.entry_modes,
            "status": a.status,
        })
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def run_app(
    app_id: str,
    action: str,
    offer_id: str,
    strategy_unit_id: str | None = None,
    language: str = "zh-CN",
    config_id: str | None = None,
    question: str = "",
    style_id: str = "professional",
    topic: str = "",
    goal: str = "reach_growth",
    tone: str = "",
    word_count: int = 150,
    cta: str = "",
    industry: str = "",
    reference: str = "",
    extra_req: str = "",
) -> str:
    """Run an app's action. Available apps and actions:

    kb_qa:
      - ask: Answer a question based on offer knowledge base.
        Required: question. Optional: style_id (professional|friendly|expert).
        Returns: answer, referenced_knowledge, has_relevant_knowledge.

    script_writer:
      - suggest_topic: Suggest a creative video script topic.
        Optional: goal, strategy_unit_id.
        Returns: topic text.
      - generate: Generate a spoken-word video script.
        Optional: topic, goal, tone, word_count, cta, industry, reference, extra_req, strategy_unit_id.
        Returns: script text, knowledge_count.

    topic_studio:
      - generate: Generate structured topic plans.
        Optional: strategy_unit_id, count (via word_count param, default 5).
        Returns: list of topic plans with title, angle, hook, key_points.

    goal must be one of: reach_growth, lead_generation, conversion, education, traffic_redirect, other.
    """
    oid = uuid.UUID(offer_id)
    suid = uuid.UUID(strategy_unit_id) if strategy_unit_id else None

    if app_id == "kb_qa":
        if action != "ask":
            return json.dumps({"error": f"Unknown action '{action}' for kb_qa. Available: ask"})
        from app.application.kb_qa_service import KBQAService
        from app.schemas.app import KBQAAskRequest

        async with _session_factory() as session:
            svc = KBQAService(session)
            req = KBQAAskRequest(
                offer_id=oid,
                question=question,
                style_id=style_id,
                language=language,
                config_id=config_id,
            )
            result = await svc.ask(req)
            return _serialize(result)

    elif app_id == "script_writer":
        if action == "suggest_topic":
            from app.application.script_writer_service import ScriptWriterService

            async with _session_factory() as session:
                svc = ScriptWriterService(session)
                topic_text = await svc.suggest_topic(
                    offer_id=offer_id,
                    strategy_unit_id=strategy_unit_id,
                    goal=goal,
                    language=language,
                    config_id=config_id,
                )
                return json.dumps({"topic": topic_text}, ensure_ascii=False)

        elif action == "generate":
            from app.application.script_writer_service import (
                DEFAULT_SYSTEM_PROMPT_EN,
                DEFAULT_SYSTEM_PROMPT_ZH,
                ScriptWriterService,
            )
            from app.schemas.app import ScriptWriterRequest

            sys_prompt = DEFAULT_SYSTEM_PROMPT_EN if language.startswith("en") else DEFAULT_SYSTEM_PROMPT_ZH
            async with _session_factory() as session:
                svc = ScriptWriterService(session)
                req = ScriptWriterRequest(
                    offer_id=oid,
                    strategy_unit_id=suid,
                    system_prompt=sys_prompt,
                    topic=topic,
                    goal=goal,
                    tone=tone or None,
                    word_count=word_count,
                    cta=cta or None,
                    industry=industry or None,
                    reference=reference or None,
                    extra_req=extra_req or None,
                    language=language,
                    config_id=config_id,
                )
                result = await svc.generate(req)
                return json.dumps(result, ensure_ascii=False, indent=2)
        else:
            return json.dumps({"error": f"Unknown action '{action}' for script_writer. Available: suggest_topic, generate"})

    elif app_id == "topic_studio":
        if action != "generate":
            return json.dumps({"error": f"Unknown action '{action}' for topic_studio. Available: generate"})
        from app.application.topic_plan_service import TopicPlanService
        from app.schemas.topic_plan import TopicPlanGenerateRequest, TopicPlanResponse

        async with _session_factory() as session:
            svc = TopicPlanService(session)
            req = TopicPlanGenerateRequest(
                offer_id=oid,
                strategy_unit_id=suid,
                count=word_count if word_count <= 20 else 5,
                language=language,
            )
            plans, thinking = await svc.generate(req)
            await session.commit()
            serialized = [TopicPlanResponse.model_validate(p, from_attributes=True).model_dump(mode="json") for p in plans]
            result = {"plans": serialized, "thinking": thinking}
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    else:
        available = ["kb_qa", "script_writer", "topic_studio"]
        return json.dumps({"error": f"Unknown app_id '{app_id}'. Available: {available}"})


# ── Composite Tools ────────────────────────────────────────────


@mcp.tool()
async def get_merchant_overview(merchant_id: str) -> str:
    """Get a complete overview of a merchant in one call:
    basic info, all offers, brand kits, and knowledge/asset counts.
    Use this as the first call to understand an enterprise before diving deeper."""
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService
    from app.application.brandkit_service import BrandKitService
    from app.application.knowledge_service import KnowledgeService
    from app.application.merchant_service import MerchantService
    from app.application.offer_service import OfferService
    from app.schemas.brandkit import BrandKitResponse
    from app.schemas.merchant import MerchantResponse
    from app.schemas.offer import OfferResponse

    mid = uuid.UUID(merchant_id)
    async with _session_factory() as session:
        merchant_svc = MerchantService(session)
        merchant = await merchant_svc.get(mid)
        merchant_data = MerchantResponse.model_validate(merchant, from_attributes=True).model_dump(mode="json")

        offer_svc = OfferService(session)
        offers, offer_total = await offer_svc.list(merchant_id=mid, page=1, page_size=100)
        offers_data = [OfferResponse.model_validate(o, from_attributes=True).model_dump(mode="json") for o in offers]

        bk_svc = BrandKitService(session)
        brandkits, bk_total = await bk_svc.list(scope_type="merchant", scope_id=mid, page=1, page_size=100)
        brandkits_data = [BrandKitResponse.model_validate(b, from_attributes=True).model_dump(mode="json") for b in brandkits]

        knowledge_svc = KnowledgeService(session)
        _, knowledge_total = await knowledge_svc.list(scope_type="merchant", scope_id=mid, page=1, page_size=1)

        storage = LocalStorageAdapter()
        asset_svc = AssetService(session, storage)
        _, asset_total = await asset_svc.list(scope_type="merchant", scope_id=mid, page=1, page_size=1)

        # Also count knowledge/assets per offer
        for od in offers_data:
            oid = uuid.UUID(od["id"])
            _, ok_total = await knowledge_svc.list(scope_type="offer", scope_id=oid, page=1, page_size=1)
            _, oa_total = await asset_svc.list(scope_type="offer", scope_id=oid, page=1, page_size=1)
            od["knowledge_count"] = ok_total
            od["asset_count"] = oa_total

        overview = {
            "merchant": merchant_data,
            "offers": {"total": offer_total, "items": offers_data},
            "brand_kits": {"total": bk_total, "items": brandkits_data},
            "merchant_knowledge_count": knowledge_total,
            "merchant_asset_count": asset_total,
        }
        return json.dumps(overview, ensure_ascii=False, indent=2, default=str)


# ── Resources ─────────────────────────────────────────────────


@mcp.resource("merchant://{merchant_id}/profile")
async def merchant_profile_resource(merchant_id: str) -> str:
    """Merchant profile including basic info, brand positioning, and compliance notes."""
    from app.application.merchant_service import MerchantService
    from app.schemas.merchant import MerchantResponse

    async with _session_factory() as session:
        svc = MerchantService(session)
        merchant = await svc.get(uuid.UUID(merchant_id))
        return _serialize(merchant, MerchantResponse)


@mcp.resource("offer://{offer_id}/context")
async def offer_context_resource(offer_id: str) -> str:
    """Full offer context: merchant info, knowledge, assets, selling points, audiences.
    Attach this resource to give an agent comprehensive product context."""
    from app.application.context_service import ContextService

    async with _session_factory() as session:
        svc = ContextService(session)
        ctx = await svc.get_offer_context(uuid.UUID(offer_id))
        return _serialize(ctx)


# ── Prompts ───────────────────────────────────────────────────


@mcp.prompt()
async def onboard_merchant(merchant_id: str) -> str:
    """Help me quickly understand a merchant's products and marketing strategy.
    Guides the agent through: merchant overview → offers → knowledge → brand kit."""
    return (
        f"I want to understand the merchant with ID {merchant_id}. "
        "Please follow these steps:\n"
        "1. Call get_merchant_overview to get the full picture of this enterprise.\n"
        "2. For each offer, summarize: what it is, who it targets, key selling points.\n"
        "3. Highlight any brand kit guidelines (tone, visual dos/don'ts).\n"
        "4. Identify gaps: offers without knowledge items, missing brand kits, etc.\n"
        "5. Give me a concise briefing I can act on."
    )


@mcp.prompt()
async def content_brief(offer_id: str, channel: str = "general", language: str = "zh-CN") -> str:
    """Generate a content planning brief for a specific offer.
    Combines offer context with topic generation to produce an actionable brief."""
    return (
        f"I need a content brief for offer ID {offer_id} targeting the '{channel}' channel "
        f"in {language}. Please:\n"
        "1. First call get_offer_context_summary to understand the product.\n"
        "2. Summarize the offer's positioning, audiences, and key selling points.\n"
        "3. Call run_app(app_id='topic_studio', action='generate', offer_id=...) to get 5 topic ideas.\n"
        "4. For each topic, explain why it fits the target audience and channel.\n"
        "5. Recommend which topic to prioritize and outline next steps."
    )


if __name__ == "__main__":
    mcp.run()
