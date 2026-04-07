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
    )
    try:
        plans, thinking = await svc.generate(request)
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
            language=data.get("language", "zh-CN"),
            config_id=data.get("config_id"),
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
