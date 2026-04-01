from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ai import AIAdapter

logger = logging.getLogger(__name__)
from app.application.context_service import ContextService
from app.exceptions import NotFoundError
from app.infrastructure.strategy_unit_link_repo import StrategyUnitKnowledgeLinkRepository
from app.infrastructure.strategy_unit_repo import StrategyUnitRepository
from app.infrastructure.topic_plan_repo import TopicPlanRepository
from app.models.topic_plan import TopicPlan
from app.schemas.topic_plan import TopicPlanGenerateRequest


class TopicPlanService:
    def __init__(self, session: AsyncSession, ai_adapter: AIAdapter | None = None):
        self.session = session
        self.repo = TopicPlanRepository(session)
        self.ai = ai_adapter

    async def generate(self, request: TopicPlanGenerateRequest) -> tuple[list[TopicPlan], str | None]:
        if not self.ai:
            from app.adapters.ai import get_ai_adapter
            self.ai = await get_ai_adapter(self.session, scene_key="topic_studio", config_id=request.config_id)

        logger.info("Topic Studio: using adapter %s/%s for offer %s",
                     getattr(self.ai, 'provider', '?'), getattr(self.ai, 'model', '?'), request.offer_id)

        # 1. Build offer context
        ctx_service = ContextService(self.session)
        context = await ctx_service.get_offer_context(request.offer_id)
        context_dict = context.model_dump(mode="json")

        ki_count = len(context_dict.get("knowledge_items", []))
        asset_count = len(context_dict.get("assets", []))
        logger.info("Topic Studio: context ready, %d knowledge items, %d assets", ki_count, asset_count)

        # 2. Build strategy unit context if provided
        strategy_unit_context: dict | None = None
        if request.strategy_unit_id:
            su_repo = StrategyUnitRepository(self.session)
            su = await su_repo.get_by_id(request.strategy_unit_id)
            if su:
                # Load knowledge items linked to this strategy unit
                link_repo = StrategyUnitKnowledgeLinkRepository(self.session)
                links, _ = await link_repo.list_by_strategy_unit(su.id, offset=0, limit=50)
                linked_ki = [
                    {
                        "knowledge_type": lnk.knowledge_item.knowledge_type if lnk.knowledge_item else "general",
                        "title": lnk.knowledge_item.title if lnk.knowledge_item else "",
                        "content_raw": lnk.knowledge_item.content_raw if lnk.knowledge_item else "",
                    }
                    for lnk in links
                    if lnk.knowledge_item
                ]
                strategy_unit_context = {
                    "id": str(su.id),
                    "name": su.name,
                    "audience_segment": su.audience_segment,
                    "scenario": su.scenario,
                    "marketing_objective": su.marketing_objective,
                    "channel": su.channel,
                    "notes": su.notes,
                    "knowledge_items": linked_ki or None,  # None → fallback to offer KB
                }

        if strategy_unit_context:
            logger.info("Topic Studio: strategy_unit=%s, audience=%s, scenario=%s",
                         strategy_unit_context.get("name", "?"),
                         strategy_unit_context.get("audience_segment", "?"),
                         strategy_unit_context.get("scenario", "?"))

        # 3. Generate plans via AI adapter
        raw_plans = await self.ai.generate_topic_plans(
            offer_context=context_dict,
            count=request.count,
            channel=request.channel,
            language=request.language,
            strategy_unit_context=strategy_unit_context,
        )

        logger.info("Topic Studio: generated %d plans", len(raw_plans))

        # 3. Persist each plan
        plans = []
        for raw in raw_plans:
            plan = await self.repo.create(
                merchant_id=context.offer.merchant_id,
                offer_id=request.offer_id,
                source_mode=raw.get("source_mode", "kb"),
                title=raw["title"],
                angle=raw.get("angle"),
                target_audience_json=raw.get("target_audience"),
                target_scenario_json=raw.get("target_scenario"),
                hook=raw.get("hook"),
                key_points_json=raw.get("key_points"),
                recommended_asset_ids_json=raw.get("recommended_asset_ids"),
                channel=raw.get("channel") or request.channel,
                language=request.language,
                score_relevance=raw.get("score_relevance"),
                score_conversion=raw.get("score_conversion"),
                score_asset_readiness=raw.get("score_asset_readiness"),
                strategy_unit_id=request.strategy_unit_id,
            )
            plans.append(plan)

        return plans, getattr(self.ai, "last_thinking", None)

    async def get(self, plan_id: uuid.UUID) -> TopicPlan:
        plan = await self.repo.get_by_id(plan_id)
        if not plan:
            raise NotFoundError("TopicPlan", str(plan_id))
        return plan

    async def list(
        self,
        offer_id: uuid.UUID | None = None,
        strategy_unit_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TopicPlan], int]:
        offset = (page - 1) * page_size
        return await self.repo.list(offer_id=offer_id, strategy_unit_id=strategy_unit_id, offset=offset, limit=page_size)
