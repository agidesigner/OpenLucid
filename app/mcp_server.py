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
        "OpenLucid — the user's marketing data and creation hub.\n"
        "Data model: Merchant → Offer → Knowledge / Assets / BrandKit / StrategyUnit.\n"
        "\n"
        "Read workflow: get_merchant_overview → get_offer_context_summary → "
        "list_apps + get_app_config(app_id) to discover run_app parameters → run_app "
        "(kb_qa / script_writer / topic_studio / ...).\n"
        "\n"
        "Capture workflow: whenever you produce a complete, deliverable piece of "
        "content using OpenLucid data — a post, a script, an email, a hook — call "
        "save_creation to store it back in the user's library. Don't wait for the "
        "user to say 'this works'; chat scrolls away, the library is what stays. "
        "Save every completed version (use tags like 'v1'/'v2' or a platform name "
        "to distinguish variants). Skip half-finished drafts, analysis notes, and "
        "tool call logs.\n"
        "\n"
        "Guided prompts: 'onboard_merchant' or 'content_brief'. "
        "Persistent context: attach merchant:// or offer:// resources."
    ),
)

# Module-level session factory reference; tests can monkey-patch this.
_session_factory = async_session_factory

# Strong references to fire-and-forget background tasks. asyncio only weakly
# references tasks, so without this set they may be GC'd mid-run.
_BACKGROUND_TASKS: set = set()


# Known-bad / placeholder APP_URL hosts: if Settings hasn't been configured
# with a real public URL, any preview_url we hand to an agent would be
# unreachable and confusing. We detect and omit those URLs instead.
_APP_URL_PLACEHOLDER_HOSTS = (
    "nihao.com",       # historical test value seen in dev envs
    "example.com",
    "change-me",
)


def _app_url_looks_valid() -> bool:
    """Return True if settings.APP_URL appears to be a reachable URL that an
    agent can actually open. Reject empty, placeholder domains, or bare localhost
    (which agents running on a different machine cannot reach).
    """
    url = (settings.APP_URL or "").strip().lower()
    if not url:
        return False
    for bad in _APP_URL_PLACEHOLDER_HOSTS:
        if bad in url:
            return False
    # localhost / 127.0.0.1 are fine during dev — agents on the same host can
    # reach them. Only flag them if clearly unconfigured (empty + fallback).
    return True


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
                    # Extract result count if available. All error paths now raise
                    # AppError (Wave 3), so we only look for {"total": N} success shapes.
                    count = ""
                    try:
                        parsed = json.loads(result)
                        if isinstance(parsed, dict) and "total" in parsed:
                            count = f" results={parsed['total']}"
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
        # Strip full description from list — agent should call get_offer_context_summary for details
        for item in serialized_items:
            item.pop("description", None)
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
    content_form: str = "",
    campaign_type: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """Search assets for a merchant or offer. scope_type: merchant | offer.

    Filters:
      q:             fuzzy search across filename, title, and tag values
                     (e.g. q='logo' matches '品牌Logo').
      tags:          exact tag value match across any category, comma-separated
                     (e.g. tags='品牌标识,宣传物料').
      asset_type:    filter by type: 'image' | 'video' | 'audio' | 'document' | 'copy' | 'url'.
      content_form:  filter by content_form closed-vocab id, comma-separated
                     (e.g. content_form='unboxing,review'). Discover valid ids
                     via get_app_config('asset_tagging').
      campaign_type: filter by campaign_type closed-vocab id, comma-separated
                     (e.g. campaign_type='flash_sale,bundle_discount').

    Tip: use `q` for keyword search, `tags` for arbitrary tag values across
    categories, and `content_form` / `campaign_type` for structured filters on
    those specific tag categories. Each item includes a preview_url for
    viewing/downloading the file.
    """
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService
    from app.schemas.asset import AssetResponse

    async with _session_factory() as session:
        storage = LocalStorageAdapter()
        svc = AssetService(session, storage)
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        cf_list = [t.strip() for t in content_form.split(",") if t.strip()] if content_form else None
        ct_list = [t.strip() for t in campaign_type.split(",") if t.strip()] if campaign_type else None
        items, total = await svc.search(
            q=q or None,
            tags=tag_list,
            asset_type=asset_type or None,
            scope_type=scope_type,
            scope_id=uuid.UUID(scope_id),
            content_form=cf_list,
            campaign_type=ct_list,
            page=page,
            page_size=page_size,
        )
        serialized_items = []
        for i in items:
            d = AssetResponse.model_validate(i, from_attributes=True).model_dump(mode="json")
            preview = storage.get_public_url(d["id"])
            if _app_url_looks_valid():
                d["preview_url"] = preview
            else:
                d["preview_url"] = None
                d["preview_url_note"] = "APP_URL not properly configured (Settings → MCP); agent-reachable URL unavailable."
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
async def get_app_config(app_id: str, language: str = "zh") -> str:
    """Fetch a single app's metadata + all enumerations needed to call run_app.

    Avoids the 4-round-trip discovery problem: instead of the agent guessing
    platform_id / persona_id / structure_id / goal_id, this returns them all in
    one call, localized.

    Supported app_ids:
      - script_writer:  returns enums.platforms / personas / structures / goals
      - content_studio: returns the same enums as script_writer (shared Composer)
      - kb_qa:          returns enums.styles
      - topic_studio:   returns enums.{} (no fixed enums at this time)

    Returns:
      {
        "app_id": "...",
        "name": "...",           (localized)
        "description": "...",    (localized)
        "actions": [...],        (action names accepted by run_app for this app)
        "enums": { <name>: [...] }
      }

    Raises on unknown app_id.
    """
    from app.apps.registry import AppRegistry
    from app.exceptions import AppError

    lang = language[:2] if language else "zh"

    app = AppRegistry.get_app(app_id)
    if app is None:
        raise AppError("UNKNOWN_APP", f"Unknown app_id '{app_id}'", 404)
    a = app.localized(lang)

    def _script_writer_enums() -> dict:
        from app.application.script_goals import list_goals
        from app.application.script_personas import list_personas
        from app.application.script_platforms import list_platforms
        from app.application.script_structures import list_structures

        # Platforms: sort by region relevance (zh-first for zh, en-first for en), matches REST.
        region_priority = {"zh": 0, "global": 1, "en": 2} if lang == "zh" else {"en": 0, "global": 1, "zh": 2}
        platforms_sorted = sorted(
            list_platforms(),
            key=lambda p: (region_priority.get(p.region, 99), p.id),
        )
        return {
            "platforms": [
                {
                    "id": p.id,
                    "name": p.localized_name(lang),
                    "emoji": p.emoji,
                    "region": p.region,
                    "content_type": p.content_type,
                    "aspect_ratio": p.aspect_ratio,
                    "max_script_chars": p.max_script_chars,
                }
                for p in platforms_sorted
            ],
            "personas": [
                {
                    "id": p.id,
                    "name": p.localized_name(lang),
                    "emoji": p.emoji,
                    "description": p.localized_description(lang),
                    "tags": p.tags,
                }
                for p in list_personas()
            ],
            "structures": [
                {
                    "id": s.id,
                    "name": s.localized_name(lang),
                    "emoji": s.emoji,
                    "description": s.localized_description(lang),
                    "section_ids": s.section_ids,
                }
                for s in list_structures()
            ],
            "goals": [
                {"id": g.id, "name": g.localized_name(lang), "emoji": g.emoji}
                for g in list_goals()
            ],
        }

    if app_id in ("script_writer", "content_studio"):
        enums = _script_writer_enums()
        actions = ["suggest_topic", "generate"] if app_id == "script_writer" else ["generate"]
    elif app_id == "kb_qa":
        from app.apps.kb_qa_styles import STYLE_TEMPLATES
        enums = {
            "styles": [
                {
                    "style_id": s.style_id,
                    "name": s.localized(lang).name,
                    "description": s.localized(lang).description,
                    "icon": s.icon,
                }
                for s in STYLE_TEMPLATES.values()
            ],
        }
        actions = ["ask"]
    elif app_id == "asset_tagging":
        from app.application.campaign_types import list_campaign_types
        from app.application.content_forms import list_content_forms
        enums = {
            "content_forms": [
                {
                    "id": cf.id,
                    "name": cf.localized_name(lang),
                    "emoji": cf.emoji,
                    "description": cf.localized_description(lang),
                }
                for cf in list_content_forms()
            ],
            "campaign_types": [
                {
                    "id": ct.id,
                    "name": ct.localized_name(lang),
                    "emoji": ct.emoji,
                    "description": ct.localized_description(lang),
                }
                for ct in list_campaign_types()
            ],
        }
        actions = ["extract_tags"]
    elif app_id == "topic_studio":
        enums = {}
        actions = ["generate"]
    else:
        # App is registered but we haven't mapped its enums yet — return empty dict
        enums = {}
        actions = []

    return json.dumps(
        {
            "app_id": a.app_id,
            "name": a.name,
            "description": a.description,
            "actions": actions,
            "enums": enums,
        },
        ensure_ascii=False, indent=2,
    )


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
    platform_id: str | None = None,
    persona_id: str | None = None,
    structure_id: str | None = None,
    goal_id: str | None = None,
    topic_plan_id: str | None = None,
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
      - generate: Generate a spoken-word video script. **Prefer passing the
        Composer dimensions** (platform_id / persona_id / structure_id /
        goal_id — discover valid ids via get_app_config("script_writer")) so
        you get structured output (hook/body/cta sections + B-roll plan),
        not just plain text. If any are set, the result's `structured_content`
        will be non-null.
        Optional: topic OR topic_plan_id (if both empty, generated from KB);
        topic_plan_id (the title/hook/angle/key_points of an existing plan are
        injected into the prompt when `topic` is empty); goal_id (preferred
        over legacy `goal` free-text); platform_id, persona_id, structure_id;
        tone, word_count, cta, industry, reference, extra_req, strategy_unit_id.
        Returns: {script, knowledge_count, structured_content, creation_id}.
        The resulting creation has source_app="mcp:external".

    topic_studio:
      - generate: Generate structured topic plans.
        Optional: strategy_unit_id, count (via word_count param, default 5).
        Returns: list of topic plans with title, angle, hook, key_points.
        Use list_topic_plans/get_topic_plan afterwards to fetch them again.

    goal (legacy free-text) must be one of:
      reach_growth, lead_generation, conversion, education, traffic_redirect, other,
      seeding, knowledge_sharing, brand_awareness.
    """
    from app.exceptions import AppError

    oid = uuid.UUID(offer_id)
    suid = uuid.UUID(strategy_unit_id) if strategy_unit_id else None

    if app_id == "kb_qa":
        if action != "ask":
            raise AppError("UNKNOWN_ACTION", f"Unknown action '{action}' for kb_qa. Available: ask", 400)
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
                    # Composer dimensions (optional) — any set → structured output
                    platform_id=platform_id,
                    persona_id=persona_id,
                    structure_id=structure_id,
                    goal_id=goal_id,
                    # Topic plan linkage
                    topic_plan_id=uuid.UUID(topic_plan_id) if topic_plan_id else None,
                    # MCP-originated creations are tagged distinctly so Settings
                    # and analytics can separate agent output from WebUI output.
                    source_app="mcp:external",
                )
                result = await svc.generate(req)
                return json.dumps(result, ensure_ascii=False, indent=2)
        else:
            raise AppError("UNKNOWN_ACTION", f"Unknown action '{action}' for script_writer. Available: suggest_topic, generate", 400)

    elif app_id == "topic_studio":
        if action != "generate":
            raise AppError("UNKNOWN_ACTION", f"Unknown action '{action}' for topic_studio. Available: generate", 400)
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
        raise AppError("UNKNOWN_APP", f"Unknown app_id '{app_id}'. Available: {available}", 400)


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
        # overview only returns lightweight offer summary — full description is in get_offer_context_summary
        for od in offers_data:
            oid = uuid.UUID(od["id"])
            _, ok_total = await knowledge_svc.list(scope_type="offer", scope_id=oid, page=1, page_size=1)
            _, oa_total = await asset_svc.list(scope_type="offer", scope_id=oid, page=1, page_size=1)
            od["knowledge_count"] = ok_total
            od["asset_count"] = oa_total
            od.pop("description", None)

        overview = {
            "merchant": merchant_data,
            "offers": {"total": offer_total, "items": offers_data},
            "brand_kits": {"total": bk_total, "items": brandkits_data},
            "merchant_knowledge_count": knowledge_total,
            "merchant_asset_count": asset_total,
        }
        return json.dumps(overview, ensure_ascii=False, indent=2, default=str)


# ── Creations (capture finished content back to OpenLucid) ─────


@mcp.tool()
async def save_creation(
    title: str,
    content: str,
    content_type: str = "general",
    offer_id: str | None = None,
    merchant_id: str | None = None,
    tags: str = "",
    source_note: str | None = None,
) -> str:
    """Save a finished content piece (post, script, copy, email, etc.) back to OpenLucid
    so the user can find, reuse, and reference it later. This is how content created
    in the chat returns to the user's permanent OpenLucid library.

    WHEN TO CALL (be proactive — save-all, not save-if-liked):
      - You produced any complete, deliverable piece of content (a post, a script, an
        email, a hook, a caption). Save it even if the user hasn't confirmed — chat
        scrolls away, the library is what stays.
      - You generated multiple variants on the same topic — save each variant,
        differentiating via `tags` (e.g. "v1"/"v2", or platform name "tiktok"/"xhs").
      - You iterated and produced a revised final — save the revised version (the
        earlier one is fine to keep too, it's a version trail).

    WHEN NOT TO CALL:
      - Half-finished drafts (still missing a hook, a CTA, or structure).
      - Analysis / reasoning notes / tool call logs / search result dumps.
      - You only quoted or summarized existing KB content (nothing new produced).
      - Pure exploration ("brainstorm 5 raw ideas") where nothing is a deliverable yet.
      - You'd save more than ~5 items in one turn (batch — likely over-capture).

    Args:
      title: Short, descriptive title (max 512 chars).
      content: The full content text. Plain text or markdown.
      content_type: Free-form category. Common values: post / script / email /
                    caption / hook / general. Default "general".
      offer_id: Optional offer UUID this content is for. If provided, the merchant
                is auto-derived from the offer.
      merchant_id: Optional merchant UUID. Only needed if offer_id is omitted AND
                   the deployment has multiple merchants.
      tags: Optional comma-separated tags (e.g. "tiktok,launch,hook").
      source_note: Optional one-line note about how this was created (e.g.
                   "Generated from KB about feature X, second draft after user
                   asked for shorter version").

    The source_app field is auto-populated as "mcp:external" so the user can
    distinguish externally captured content from internally generated content.

    Returns the saved creation as JSON with its assigned id.
    """
    from app.application.creation_service import CreationService
    from app.schemas.creation import CreationCreate, CreationResponse

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    async with _session_factory() as session:
        svc = CreationService(session)
        data = CreationCreate(
            title=title,
            content=content,
            content_type=content_type or "general",
            offer_id=uuid.UUID(offer_id) if offer_id else None,
            merchant_id=uuid.UUID(merchant_id) if merchant_id else None,
            tags=tag_list,
            source_app="mcp:external",
            source_note=source_note,
        )
        creation = await svc.create(data)
        await session.commit()
        return _serialize(creation, CreationResponse)


@mcp.tool()
async def list_creations(
    merchant_id: str | None = None,
    offer_id: str | None = None,
    content_type: str | None = None,
    source_app: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List finished creations saved in OpenLucid — posts, scripts, emails, etc.

    Use this to see what's already been saved for an offer before generating
    similar content, or to let the user find a prior piece to iterate on.

    Filters:
      offer_id: Only creations for this offer (UUID string).
      merchant_id: Only creations for this merchant (UUID string).
      content_type: Free-form category (post / script / email / ...).
      source_app: Who produced it. Values: "mcp:external" (from an agent),
                  "script_writer" / "content_studio" / "topic_studio" / "manual".
      q: Substring search over title + content.

    Returns: {"total", "page", "items": [...]}. Each item is a CreationResponse
    with `content` stripped (call get_creation(id) for full text); title,
    source_app, content_type, tags, created_at, structured_content (for Script
    Writer output), video_count, and latest_video are included so the agent can
    pick which creation to drill into.
    """
    from app.application.creation_service import CreationService
    from app.schemas.creation import CreationResponse

    async with _session_factory() as session:
        svc = CreationService(session)
        items, total = await svc.list(
            merchant_id=uuid.UUID(merchant_id) if merchant_id else None,
            offer_id=uuid.UUID(offer_id) if offer_id else None,
            content_type=content_type,
            source_app=source_app,
            q=q,
            page=page,
            page_size=page_size,
        )
        # Enrich with video summary so agents can spot which creations already have videos.
        summaries = await svc.get_video_summaries([c.id for c in items])
        for c in items:
            s = summaries.get(c.id)
            c.video_count = s["count"] if s else 0
            c.latest_video = s["latest"] if s else None
        serialized = [
            CreationResponse.model_validate(c, from_attributes=True).model_dump(mode="json")
            for c in items
        ]
        # List view: drop full content to keep responses small — use get_creation for full text.
        for item in serialized:
            item.pop("content", None)
        return json.dumps(
            {"total": total, "page": page, "items": serialized},
            ensure_ascii=False, indent=2, default=str,
        )


@mcp.tool()
async def get_creation(creation_id: str) -> str:
    """Fetch a single creation by id, with full `content` and `structured_content`.

    Use this after list_creations when you want to read, quote, or iterate on
    a specific prior creation. Returns the full CreationResponse including
    `content` (plain text) and `structured_content` (Script Writer sections,
    if applicable), plus video summary.
    """
    from app.application.creation_service import CreationService
    from app.schemas.creation import CreationResponse

    async with _session_factory() as session:
        svc = CreationService(session)
        creation = await svc.get(uuid.UUID(creation_id))
        summaries = await svc.get_video_summaries([creation.id])
        s = summaries.get(creation.id)
        creation.video_count = s["count"] if s else 0
        creation.latest_video = s["latest"] if s else None
        return _serialize(creation, CreationResponse)


# ── Topic Plans (generated by run_app(topic_studio), now readable back) ──


@mcp.tool()
async def list_topic_plans(
    offer_id: str | None = None,
    strategy_unit_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List previously generated topic plans — the output of run_app(topic_studio).

    Use this to recover recent topic ideas, pick one to turn into a script, or
    audit how many plans exist for an offer. Filter by offer_id and/or
    strategy_unit_id.

    Each item is a TopicPlanResponse with: id, title, angle, hook,
    key_points_json, target_audience_json, target_scenario_json, channel,
    score_relevance, score_conversion, score_asset_readiness, status, created_at.

    Feed a plan's id into run_app(script_writer, topic_plan_id=<id>) to
    auto-prefill the script prompt with the plan's title/hook/angle/key_points.
    """
    from app.application.topic_plan_service import TopicPlanService
    from app.schemas.topic_plan import TopicPlanResponse

    async with _session_factory() as session:
        svc = TopicPlanService(session)
        items, total = await svc.list(
            offer_id=uuid.UUID(offer_id) if offer_id else None,
            strategy_unit_id=uuid.UUID(strategy_unit_id) if strategy_unit_id else None,
            page=page, page_size=page_size,
        )
        serialized = [
            TopicPlanResponse.model_validate(p, from_attributes=True).model_dump(mode="json")
            for p in items
        ]
        return json.dumps(
            {"total": total, "page": page, "items": serialized},
            ensure_ascii=False, indent=2, default=str,
        )


@mcp.tool()
async def get_topic_plan(topic_id: str) -> str:
    """Fetch a single topic plan by id — full title, angle, hook, key_points,
    scoring, status.
    """
    from app.application.topic_plan_service import TopicPlanService
    from app.schemas.topic_plan import TopicPlanResponse

    async with _session_factory() as session:
        svc = TopicPlanService(session)
        plan = await svc.get(uuid.UUID(topic_id))
        return _serialize(plan, TopicPlanResponse)


# ── Videos (full generation workflow for agents) ───────────────


@mcp.tool()
async def list_media_providers() -> str:
    """List configured video/image/TTS providers (Chanjing, Jogg, ...).

    Agent uses this to find `provider_config_id` for subsequent calls.
    Returns [{id, provider, label, is_active}, ...] — credentials are not exposed.
    """
    from app.application.media_provider_service import list_media_provider_configs

    async with _session_factory() as session:
        configs = await list_media_provider_configs(session)
        items = [
            {
                "id": c.id,
                "provider": c.provider,
                "label": c.label,
                "is_active": c.is_active,
            }
            for c in configs
        ]
        return json.dumps({"total": len(items), "items": items}, ensure_ascii=False, indent=2)


@mcp.tool()
async def list_avatars(provider_config_id: str, page: int = 1, page_size: int = 50) -> str:
    """List avatars available under a provider config — for picking `avatar_id`
    before calling submit_video. Hits the upstream provider live.

    Returns {total, page, items: [{id, name, gender, age, preview_image_url,
    preview_video_url, extras}]}.
    """
    from app.application.media_provider_service import list_avatars_for_config

    async with _session_factory() as session:
        avatars = await list_avatars_for_config(
            session, uuid.UUID(provider_config_id), page=page, page_size=page_size,
        )
        items = [a.model_dump(mode="json") for a in avatars]
        return json.dumps(
            {"total": len(items), "page": page, "items": items},
            ensure_ascii=False, indent=2, default=str,
        )


@mcp.tool()
async def list_voices(provider_config_id: str, page: int = 1, page_size: int = 50) -> str:
    """List voices available under a provider config — for picking `voice_id`
    before calling submit_video.

    Returns {total, page, items: [{id, name, gender, age, language, sample_url}]}.
    """
    from app.application.media_provider_service import list_voices_for_config

    async with _session_factory() as session:
        voices = await list_voices_for_config(
            session, uuid.UUID(provider_config_id), page=page, page_size=page_size,
        )
        items = [v.model_dump(mode="json") for v in voices]
        return json.dumps(
            {"total": len(items), "page": page, "items": items},
            ensure_ascii=False, indent=2, default=str,
        )


@mcp.tool()
async def submit_video(
    creation_id: str,
    provider_config_id: str,
    avatar_id: str,
    voice_id: str,
    script: str,
    aspect_ratio: str = "portrait",
    caption: bool = True,
    name: str | None = None,
    provider_extras: dict | None = None,
) -> str:
    """Kick off a talking-avatar video for an existing creation. Returns the job
    id; poll with get_video. `aspect_ratio` ∈ portrait|landscape|square.

    Discovery path: call list_media_providers → list_avatars → list_voices first.

    IMPORTANT — avatar extras pass-through:
      When you pick an avatar from `list_avatars`, copy its entire `extras`
      object into `provider_extras` here. For Chanjing this carries the
      `figure_type` that the upstream API requires; omitting it will make
      create_video fail with code=50000 "figure_type not selected correctly"
      for any public avatar whose type is not the default `whole_body`
      (e.g. sit_body / circle_view avatars). Example:
          provider_extras = {"figure_type": "sit_body"}   # copied verbatim
                                                          # from list_avatars
                                                          # item.extras

      For Jogg, pass the same `extras` dict; unused keys are ignored.
    """
    from app.application.video_service import create_video_job
    from app.schemas.video import VideoGenerateRequest

    async with _session_factory() as session:
        data = VideoGenerateRequest(
            provider_config_id=provider_config_id,
            avatar_id=avatar_id,
            voice_id=voice_id,
            script=script,
            aspect_ratio=aspect_ratio,  # type: ignore[arg-type]
            caption=caption,
            name=name,
            provider_extras=provider_extras or {},
        )
        job = await create_video_job(session, uuid.UUID(creation_id), data)
        return _serialize(job)


@mcp.tool()
async def get_video(video_id: str) -> str:
    """Get a video job's current state. Triggers a lazy refresh from the
    provider if status is non-terminal, so the returned `status` and
    `video_url` are fresh.
    """
    from app.application.video_service import get_video_job

    async with _session_factory() as session:
        job = await get_video_job(session, uuid.UUID(video_id))
        return _serialize(job)


@mcp.tool()
async def list_videos_for_creation(creation_id: str) -> str:
    """List all video jobs for a creation, oldest-first. Refreshes any
    non-terminal jobs inline.
    """
    from app.application.video_service import list_video_jobs_for_creation

    async with _session_factory() as session:
        jobs = await list_video_jobs_for_creation(session, uuid.UUID(creation_id))
        return json.dumps(
            {"total": len(jobs), "items": [j.model_dump(mode="json") for j in jobs]},
            ensure_ascii=False, indent=2, default=str,
        )


# ── Asset Writes (agent-ingestible) ────────────────────────────


@mcp.tool()
async def create_copy_asset(
    scope_type: str,
    scope_id: str,
    title: str,
    content_text: str,
    language: str = "zh-CN",
    tags: str = "",
) -> str:
    """Save a text asset (a "copy") under a scope — e.g. an agent captured a
    customer testimonial, a reference passage, a marketing doc excerpt, and
    wants it persistent in the KB.

    scope_type: "merchant" | "offer". scope_id: UUID of the scope owner.
    tags: optional comma-separated tags (stored under a generic category).
    """
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService
    from app.schemas.asset import AssetCopyCreate, AssetResponse
    from app.domain.enums import ScopeType

    tag_list: dict[str, list[str]] = {}
    if tags:
        tag_list = {"general": [t.strip() for t in tags.split(",") if t.strip()]}

    async with _session_factory() as session:
        svc = AssetService(session, LocalStorageAdapter())
        asset = await svc.create_copy(AssetCopyCreate(
            scope_type=ScopeType(scope_type),
            scope_id=uuid.UUID(scope_id),
            title=title,
            content_text=content_text,
            tags=tag_list,
            language=language,
        ))
        await session.commit()
        await session.refresh(asset)
        return _serialize(asset, AssetResponse)


@mcp.tool()
async def upload_asset_from_url(
    url: str,
    scope_type: str,
    scope_id: str,
    title: str | None = None,
    language: str = "zh-CN",
) -> str:
    """Ingest a remote file (image / video / audio / PDF / doc) by URL and save
    it as an asset. The server fetches the bytes, detects type from the
    response `Content-Type` (fallback to URL extension), persists the file, and
    kicks off the normal async parse pipeline.

    Limits: 30-second fetch timeout, 50 MB max. Raises ASSET_URL_TOO_LARGE
    or ASSET_URL_FETCH_FAILED on violations.
    """
    import httpx
    from pathlib import Path as _Path
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService
    from app.exceptions import AppError
    from app.schemas.asset import AssetResponse, AssetUploadMeta
    from app.domain.enums import AssetType, ScopeType

    MAX_BYTES = 50 * 1024 * 1024  # 50 MB

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as e:
        raise AppError("ASSET_URL_FETCH_FAILED", f"Failed to fetch {url}: {e}", 502) from e

    content_length = int(resp.headers.get("content-length") or len(resp.content))
    if content_length > MAX_BYTES:
        raise AppError(
            "ASSET_URL_TOO_LARGE",
            f"File at {url} is {content_length} bytes (max {MAX_BYTES})",
            413,
        )
    content = resp.content
    if len(content) > MAX_BYTES:
        raise AppError("ASSET_URL_TOO_LARGE", f"Body exceeds {MAX_BYTES} bytes", 413)

    # Detect content type: response header first, then URL extension
    mime_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = _Path(url.split("?")[0]).suffix.lower().lstrip(".")

    def _resolve_asset_type(mime: str, extension: str) -> AssetType:
        if mime.startswith("image/"):
            return AssetType.IMAGE
        if mime.startswith("video/"):
            return AssetType.VIDEO
        if mime.startswith("audio/"):
            return AssetType.AUDIO
        if mime in {"application/pdf", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}:
            return AssetType.DOCUMENT
        # Extension fallback
        if extension in {"jpg", "jpeg", "png", "gif", "webp", "svg"}:
            return AssetType.IMAGE
        if extension in {"mp4", "mov", "webm", "avi", "mkv"}:
            return AssetType.VIDEO
        if extension in {"mp3", "wav", "m4a", "ogg"}:
            return AssetType.AUDIO
        if extension in {"pdf", "docx", "doc", "xlsx", "xls", "csv", "txt", "md"}:
            return AssetType.DOCUMENT
        return AssetType.URL

    asset_type = _resolve_asset_type(mime_type, ext)

    # Derive a sane file name from the URL
    file_name = _Path(url.split("?")[0]).name or f"asset.{ext or 'bin'}"

    async with _session_factory() as session:
        svc = AssetService(session, LocalStorageAdapter())
        asset = await svc.upload(
            file_content=content,
            file_name=file_name,
            mime_type=mime_type or None,
            meta=AssetUploadMeta(
                scope_type=ScopeType(scope_type),
                scope_id=uuid.UUID(scope_id),
                asset_type=asset_type,
                language=language,
            ),
        )
        if title:
            await svc.repo.update(asset, title=title)
        await session.commit()
        await session.refresh(asset)
        asset_id = asset.id
        response = _serialize(asset, AssetResponse)

    # Kick background parse (metadata extraction + vision LLM tagging + slice
    # generation), matching what REST `/assets/upload` does. Fire-and-forget,
    # but hold a reference so the loop doesn't GC the weakly-referenced task.
    import asyncio as _asyncio
    from app.api.assets import _parse_in_background
    task = _asyncio.create_task(_parse_in_background(asset_id))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return response


@mcp.tool()
async def get_asset(asset_id: str) -> str:
    """Fetch an asset's full metadata by id — including title, tags, parse
    status, scores, storage_uri.
    """
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService
    from app.schemas.asset import AssetResponse

    async with _session_factory() as session:
        svc = AssetService(session, LocalStorageAdapter())
        asset = await svc.get(uuid.UUID(asset_id))
        return _serialize(asset, AssetResponse)


@mcp.tool()
async def update_asset(
    asset_id: str,
    title: str | None = None,
    tags: str = "",
) -> str:
    """Rename or retag an asset. `tags` is a comma-separated list stored under
    the "general" category (matches REST's shape).
    """
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService
    from app.schemas.asset import AssetResponse

    tags_json: dict[str, list[str]] | None = None
    if tags:
        tags_json = {"general": [t.strip() for t in tags.split(",") if t.strip()]}

    async with _session_factory() as session:
        svc = AssetService(session, LocalStorageAdapter())
        asset = await svc.update_asset(
            uuid.UUID(asset_id), title=title, tags_json=tags_json,
        )
        return _serialize(asset, AssetResponse)


@mcp.tool()
async def delete_asset(asset_id: str) -> str:
    """Permanently delete an asset and its stored files. Returns {deleted: true}."""
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService

    async with _session_factory() as session:
        svc = AssetService(session, LocalStorageAdapter())
        await svc.delete_asset(uuid.UUID(asset_id))
        return json.dumps({"deleted": True, "asset_id": asset_id}, ensure_ascii=False)


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

