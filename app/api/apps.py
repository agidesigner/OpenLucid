import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.registry import AppRegistry
from app.application.topic_plan_service import TopicPlanService
from app.database import get_db
from app.infrastructure.knowledge_repo import KnowledgeItemRepository
from app.infrastructure.offer_repo import OfferRepository
from app.infrastructure.strategy_unit_link_repo import (
    StrategyUnitAssetLinkRepository,
    StrategyUnitKnowledgeLinkRepository,
)
from app.infrastructure.strategy_unit_repo import StrategyUnitRepository
from app.apps.kb_qa_styles import STYLE_TEMPLATES
from app.application.kb_qa_service import KBQAService
from app.schemas.app import (
    AppDefinitionResponse,
    KBQAAskRequest,
    KBQAAskResponse,
    KBQAStyleResponse,
    ScriptWriterRequest,
    TopicStudioContextPreview,
    TopicStudioRunRequest,
)
from app.schemas.topic_plan import TopicPlanGenerateRequest, TopicPlanGenerateResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apps", tags=["apps"])


@router.get("/topic-studio/context-preview", response_model=TopicStudioContextPreview)
async def topic_studio_context_preview(
    offer_id: uuid.UUID = Query(...),
    strategy_unit_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    offer_repo = OfferRepository(db)
    offer = await offer_repo.get_by_id(offer_id)
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")

    knowledge_repo = KnowledgeItemRepository(db)
    _, knowledge_count = await knowledge_repo.list(
        scope_type="offer", scope_id=offer_id, offset=0, limit=1
    )

    # Asset count — use AssetRepository if available, otherwise default 0
    asset_count = 0
    try:
        from app.infrastructure.asset_repo import AssetRepository
        asset_repo = AssetRepository(db)
        _, asset_count = await asset_repo.list(
            scope_type="offer", scope_id=offer_id, offset=0, limit=1
        )
    except (ImportError, Exception):
        pass

    unit_name = None
    audience_segment = None
    scenario = None
    channel = None
    marketing_objective = None
    linked_knowledge_count = 0
    linked_asset_count = 0

    if strategy_unit_id:
        su_repo = StrategyUnitRepository(db)
        unit = await su_repo.get_by_id(strategy_unit_id)
        if unit:
            unit_name = unit.name
            audience_segment = unit.audience_segment
            scenario = unit.scenario
            channel = unit.channel
            marketing_objective = unit.marketing_objective

            k_link_repo = StrategyUnitKnowledgeLinkRepository(db)
            _, linked_knowledge_count = await k_link_repo.list_by_strategy_unit(
                strategy_unit_id, offset=0, limit=1
            )

            a_link_repo = StrategyUnitAssetLinkRepository(db)
            _, linked_asset_count = await a_link_repo.list_by_strategy_unit(
                strategy_unit_id, offset=0, limit=1
            )

            # Fall back to offer-level counts when no unit-level links exist
            if linked_knowledge_count == 0:
                linked_knowledge_count = knowledge_count
            if linked_asset_count == 0:
                linked_asset_count = asset_count

    return TopicStudioContextPreview(
        offer_id=offer_id,
        offer_name=offer.name,
        strategy_unit_id=strategy_unit_id,
        unit_name=unit_name,
        audience_segment=audience_segment,
        scenario=scenario,
        channel=channel,
        marketing_objective=marketing_objective,
        knowledge_count=knowledge_count,
        linked_knowledge_count=linked_knowledge_count,
        asset_count=asset_count,
        linked_asset_count=linked_asset_count,
        is_ready=linked_knowledge_count > 0 if strategy_unit_id else knowledge_count > 0,
    )


@router.post("/topic-studio/run/stream")
async def topic_studio_run_stream(
    data: TopicStudioRunRequest,
    db: AsyncSession = Depends(get_db),
):
    """SSE variant of topic-studio/run. Emits ``hotspot``, ``plan``,
    ``thinking``, ``done``, and ``error`` events. Each plan event is
    persisted before being streamed, so what the user sees in the
    grid is always real DB rows (page refresh shows them in history).

    The non-streaming endpoint stays available for MCP / programmatic
    callers that prefer a single JSON response.
    """
    svc = TopicPlanService(db)
    request = TopicPlanGenerateRequest(
        offer_id=data.offer_id,
        strategy_unit_id=data.strategy_unit_id,
        count=data.count,
        language=data.language,
        channel=data.channel,
        config_id=data.config_id,
        model_override=data.model_override,
        instruction=data.instruction,
        external_context_text=data.external_context_text,
        external_context_url=data.external_context_url,
    )
    return StreamingResponse(
        svc.generate_stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/topic-studio/run", response_model=TopicPlanGenerateResponse, status_code=201)
async def topic_studio_run(
    data: TopicStudioRunRequest,
    db: AsyncSession = Depends(get_db),
):
    svc = TopicPlanService(db)
    request = TopicPlanGenerateRequest(
        offer_id=data.offer_id,
        strategy_unit_id=data.strategy_unit_id,
        count=data.count,
        language=data.language,
        channel=data.channel,
        config_id=data.config_id,
        model_override=data.model_override,
        instruction=data.instruction,
        external_context_text=data.external_context_text,
        external_context_url=data.external_context_url,
    )
    try:
        plans, thinking, hotspot = await svc.generate(request)
    except Exception as e:
        # Extract readable message from upstream LLM errors (e.g. httpx, openai)
        detail = str(e)
        resp = getattr(e, "response", None)
        if resp is not None:
            try:
                body = resp.json()
                detail = body.get("message") or body.get("error") or body.get("detail") or detail
            except Exception:
                pass
        raise HTTPException(status_code=400, detail=detail) from e
    return TopicPlanGenerateResponse(
        offer_id=data.offer_id,
        count=len(plans),
        plans=plans,
        thinking=thinking,
        hotspot=hotspot,
    )


# ── KB QA ──────────────────────────────────────────────────────


@router.get("/kb-qa/styles", response_model=list[KBQAStyleResponse])
async def kb_qa_styles(lang: str = Query("zh", pattern="^(zh|en)$")):
    return [
        KBQAStyleResponse(
            style_id=s.style_id, name=ls.name,
            description=ls.description, icon=s.icon,
        )
        for s in STYLE_TEMPLATES.values()
        for ls in [s.localized(lang)]
    ]


@router.post("/kb-qa/ask", response_model=KBQAAskResponse)
async def kb_qa_ask(
    data: KBQAAskRequest,
    db: AsyncSession = Depends(get_db),
):
    svc = KBQAService(db)
    return await svc.ask(data)


@router.post("/kb-qa/ask/stream")
async def kb_qa_ask_stream(
    data: KBQAAskRequest,
    db: AsyncSession = Depends(get_db),
):
    svc = KBQAService(db)
    return StreamingResponse(
        svc.ask_stream(data),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Script Writer ─────────────────────────────────────────────


@router.get("/script-writer/platforms")
async def script_writer_list_platforms(lang: str = Query("zh", pattern="^(zh|en)$")):
    from app.application.script_platforms import list_platforms
    # Two-tier sort for the dropdown:
    #
    # TIER 1 — region relevance to the user's UI language:
    #   zh user: zh platforms first, then global, then en-only
    #   en user: en platforms first, then global, then zh-only
    #
    # TIER 2 — usage priority within the region group, chosen by
    # real-world marketing use (not alphabetical, which surfaced
    # "Discord" as the en default and "公众号" as the zh default —
    # both wrong for a marketing content tool). The ID order inside
    # each list below is the intended display order; anything not
    # listed falls to the end, alphabetical by id.
    platform_priority = {
        "zh": ["xiaohongshu", "wechat_gzh", "blog", "wechat_video", "douyin", "tiktok", "youtube_shorts",
               "linkedin", "substack", "instagram_carousel", "x_twitter", "reddit", "discord"],
        "en": ["linkedin", "substack", "instagram_carousel", "x_twitter", "reddit", "discord", "blog",
               "tiktok", "youtube_shorts", "wechat_gzh", "xiaohongshu", "wechat_video", "douyin"],
    }
    order_index = {pid: i for i, pid in enumerate(platform_priority[lang])}

    if lang == "zh":
        region_priority = {"zh": 0, "global": 1, "en": 2}
    else:
        region_priority = {"en": 0, "global": 1, "zh": 2}

    sorted_platforms = sorted(
        list_platforms(),
        key=lambda p: (
            region_priority.get(p.region, 99),
            order_index.get(p.id, 999),
            p.id,
        ),
    )
    return [
        {
            "id": p.id,
            "name": p.localized_name(lang),
            "emoji": p.emoji,
            "region": p.region,
            "content_type": p.content_type,
            "aspect_ratio": p.aspect_ratio,
            "max_script_chars": p.max_script_chars,
        }
        for p in sorted_platforms
    ]


@router.get("/script-writer/personas")
async def script_writer_list_personas(lang: str = Query("zh", pattern="^(zh|en)$")):
    from app.application.script_personas import list_personas
    return [
        {
            "id": p.id,
            "name": p.localized_name(lang),
            "emoji": p.emoji,
            "description": p.localized_description(lang),
            "tags": p.tags,
        }
        for p in list_personas()
    ]


@router.get("/script-writer/structures")
async def script_writer_list_structures(lang: str = Query("zh", pattern="^(zh|en)$")):
    from app.application.script_structures import list_structures
    return [
        {
            "id": s.id,
            "name": s.localized_name(lang),
            "emoji": s.emoji,
            "description": s.localized_description(lang),
            "section_ids": s.section_ids,
        }
        for s in list_structures()
    ]


@router.get("/script-writer/goals")
async def script_writer_list_goals(lang: str = Query("zh", pattern="^(zh|en)$")):
    from app.application.script_goals import list_goals
    return [
        {
            "id": g.id,
            "name": g.localized_name(lang),
            "emoji": g.emoji,
        }
        for g in list_goals()
    ]


# ── Asset Tagging — closed-vocabulary enums ──────────────────


@router.get("/asset-tagging/content-forms")
async def asset_tagging_list_content_forms(lang: str = Query("zh", pattern="^(zh|en)$")):
    from app.application.content_forms import list_content_forms
    return [
        {
            "id": cf.id,
            "name": cf.localized_name(lang),
            "emoji": cf.emoji,
            "description": cf.localized_description(lang),
        }
        for cf in list_content_forms()
    ]


@router.get("/asset-tagging/campaign-types")
async def asset_tagging_list_campaign_types(lang: str = Query("zh", pattern="^(zh|en)$")):
    from app.application.campaign_types import list_campaign_types
    return [
        {
            "id": ct.id,
            "name": ct.localized_name(lang),
            "emoji": ct.emoji,
            "description": ct.localized_description(lang),
        }
        for ct in list_campaign_types()
    ]


@router.post("/script-writer/suggest-topic")
async def script_writer_suggest_topic(
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    from app.application.script_writer_service import ScriptWriterService

    svc = ScriptWriterService(db)
    try:
        topic = await svc.suggest_topic(
            offer_id=data["offer_id"],
            strategy_unit_id=data.get("strategy_unit_id"),
            goal=data.get("goal", "reach_growth"),
            # Omit/null ``language`` → service follows KB detection.
            language=data.get("language") or None,
            config_id=data.get("config_id"),
            model_override=data.get("model_override"),
        )
    except Exception as e:
        logger.exception("suggest_topic failed")
        raise HTTPException(status_code=500, detail=str(e))
    return {"topic": topic}


@router.post("/script-writer/generate/stream")
async def script_writer_generate_stream(
    data: ScriptWriterRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.application.script_writer_service import ScriptWriterService

    svc = ScriptWriterService(db)
    return StreamingResponse(
        svc.generate_stream(data),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Generic ────────────────────────────────────────────────────


@router.get("", response_model=list[AppDefinitionResponse])
async def list_apps(lang: str = Query("zh", pattern="^(zh|en)$")):
    return [app.localized(lang) for app in AppRegistry.list_apps()]


@router.get("/{app_id}", response_model=AppDefinitionResponse)
async def get_app(app_id: str, lang: str = Query("zh", pattern="^(zh|en)$")):
    definition = AppRegistry.get_app(app_id)
    if not definition:
        raise HTTPException(status_code=404, detail="App not found")
    return definition.localized(lang)
