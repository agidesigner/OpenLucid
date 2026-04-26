from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ai import AIAdapter

logger = logging.getLogger(__name__)
from app.application.context_service import ContextService
from app.exceptions import NotFoundError
from app.infrastructure.strategy_unit_link_repo import StrategyUnitKnowledgeLinkRepository
from app.infrastructure.strategy_unit_repo import StrategyUnitRepository
from app.infrastructure.topic_plan_repo import TopicPlanRepository
from app.models.topic_plan import TopicPlan
from app.schemas.topic_plan import HotspotSummary, TopicPlanGenerateRequest, TopicPlanResponse


class TopicPlanService:
    def __init__(self, session: AsyncSession, ai_adapter: AIAdapter | None = None):
        self.session = session
        self.repo = TopicPlanRepository(session)
        self.ai = ai_adapter

    async def generate(
        self, request: TopicPlanGenerateRequest
    ) -> tuple[list[TopicPlan], str | None, HotspotSummary | None]:
        if not self.ai:
            from app.adapters.ai import get_ai_adapter
            self.ai = await get_ai_adapter(
                self.session,
                scene_key="topic_studio",
                config_id=request.config_id,
                model_override=request.model_override,
            )

        logger.info("Topic Studio: using adapter %s/%s for offer %s",
                     getattr(self.ai, 'provider', '?'), getattr(self.ai, 'model', '?'), request.offer_id)

        # 1. Build offer context
        ctx_service = ContextService(self.session)
        context = await ctx_service.get_offer_context(request.offer_id)
        context_dict = context.model_dump(mode="json")

        ki_count = len(context_dict.get("knowledge_items", []))
        asset_count = len(context_dict.get("assets", []))
        logger.info("Topic Studio: context ready, %d knowledge items, %d assets", ki_count, asset_count)

        # KB-centric language: override request.language with the KB's
        # Presence-of-language rule: explicit API value wins, else KB.
        from app.libs.lang_detect import resolve_output_language
        kb_sample = " ".join(
            (k.get("title") or "") + " " + ((k.get("content_raw") or "")[:500])
            for k in context_dict.get("knowledge_items", [])[:15]
        )
        request.language = resolve_output_language(
            request.language, kb_sample, caller="topic_studio",
        )

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

        # 3. Fetch existing topic titles for dedup (same language only)
        existing_plans, _ = await self.repo.list(
            offer_id=request.offer_id,
            strategy_unit_id=request.strategy_unit_id,
            language=request.language,
            offset=0,
            limit=50,
        )
        existing_titles = [p.title for p in existing_plans if p.title]

        # Liked/disliked topics across the entire offer, all languages
        # (style preference is language-agnostic)
        liked_plans = await self.repo.list_rated(request.offer_id, rating=1, limit=20)
        liked_topics = [{"title": p.title, "angle": p.angle} for p in liked_plans if p.title]
        disliked_plans = await self.repo.list_rated(request.offer_id, rating=-1, limit=20)
        disliked_topics = [{"title": p.title, "angle": p.angle} for p in disliked_plans if p.title]

        logger.info("Topic Studio: dedup=%d existing (%s), %d liked, %d disliked",
                     len(existing_titles), request.language, len(liked_topics), len(disliked_topics))

        # 4. Generate plans via AI adapter (with brand voice overlay if set)
        brand_voice = await ctx_service.resolve_brand_voice(request.offer_id)
        external_ctx = (request.external_context_text or "").strip() or None
        raw_plans = await self.ai.generate_topic_plans(
            offer_context=context_dict,
            count=request.count,
            channel=request.channel,
            language=request.language,
            strategy_unit_context=strategy_unit_context,
            existing_titles=existing_titles or None,
            liked_titles=liked_topics or None,
            disliked_titles=disliked_topics or None,
            user_instruction=request.instruction,
            brand_voice=brand_voice,
            external_context_text=external_ctx,
        )

        # When the LLM ran in trend-bridge mode it may emit a leading
        # ``__hotspot__`` entry carrying the structured read of the trend.
        # Pop it out before logging/persisting so it doesn't get counted
        # as a plan.
        hotspot: HotspotSummary | None = None
        if external_ctx and raw_plans and raw_plans[0].get("__hotspot__"):
            hs = raw_plans.pop(0)["__hotspot__"]
            try:
                hotspot = HotspotSummary(**hs) if isinstance(hs, dict) else None
            except Exception:
                logger.warning("Topic Studio: hotspot payload didn't parse, dropping: %r", hs)

        logger.info(
            "Topic Studio: generated %d plans (trend_bridge=%s, requested=%d)",
            len(raw_plans), bool(external_ctx), request.count,
        )

        # 3. Persist each plan
        plans = []
        default_source_mode = "trend_bridge" if external_ctx else "kb"
        # Hotspot is shared across plans in this batch; copy onto each
        # row so plans are self-contained when fetched by id later.
        hotspot_payload_for_db = hotspot.model_dump(exclude_none=True) if hotspot else None
        for raw in raw_plans:
            plan = await self.repo.create(
                merchant_id=context.offer.merchant_id,
                offer_id=request.offer_id,
                source_mode=raw.get("source_mode", default_source_mode),
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
                # Trend-bridge persistence — script writer reads these
                # via topic_plan_id to keep the trend context alive
                # downstream.
                hotspot_json=hotspot_payload_for_db,
                do_not_associate_json=raw.get("do_not_associate"),
                relevance_tier=raw.get("relevance_tier"),
                risk_of_forced_relevance=raw.get("risk_of_forced_relevance"),
            )
            # ``hotspot`` and ``do_not_associate`` are read-only properties
            # on TopicPlan that delegate to the *_json columns, so
            # TopicPlanResponse.from_attributes picks them up automatically.
            plans.append(plan)

        return plans, getattr(self.ai, "last_thinking", None), hotspot

    async def generate_stream(self, request: TopicPlanGenerateRequest) -> AsyncIterator[str]:
        """Stream topic plans as SSE events while the LLM is still
        producing them. Each ``plan`` event is persisted before being
        emitted so the client's just-rendered card is real, not a
        ghost. Falls back to non-streaming for adapters that don't
        support token streaming (Stub etc.) by running the regular
        ``generate`` and replaying the output as synthetic SSE events.
        """
        from app.adapters.ai import OpenAICompatibleAdapter, get_ai_adapter

        # First event — emit IMMEDIATELY so the client knows we're alive
        # while we build context and dial the LLM. Connecting to the LLM
        # provider can take 5-30s on cold paths or via proxies; without
        # this heartbeat the UI sits on "5%" with no signal.
        yield f"event: started\ndata: {json.dumps({'phase': 'preparing'})}\n\n"

        if not self.ai:
            self.ai = await get_ai_adapter(
                self.session,
                scene_key="topic_studio",
                config_id=request.config_id,
                model_override=request.model_override,
            )

        # Non-streaming adapters → fall back to one-shot generate, replay as SSE
        if not isinstance(self.ai, OpenAICompatibleAdapter):
            try:
                plans, thinking, hotspot = await self.generate(request)
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n"
                return
            if hotspot:
                yield f"event: hotspot\ndata: {json.dumps(hotspot.model_dump(exclude_none=True), ensure_ascii=False)}\n\n"
            for p in plans:
                payload = TopicPlanResponse.model_validate(p, from_attributes=True).model_dump(mode="json")
                yield f"event: plan\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
            yield f"event: done\ndata: {json.dumps({'count': len(plans), 'requested': request.count})}\n\n"
            return

        # Build context (mirror generate's preamble)
        ctx_service = ContextService(self.session)
        try:
            context = await ctx_service.get_offer_context(request.offer_id)
        except NotFoundError as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
            return

        context_dict = context.model_dump(mode="json")
        from app.libs.lang_detect import resolve_output_language
        kb_sample = " ".join(
            (k.get("title") or "") + " " + ((k.get("content_raw") or "")[:500])
            for k in context_dict.get("knowledge_items", [])[:15]
        )
        request.language = resolve_output_language(request.language, kb_sample, caller="topic_studio")

        strategy_unit_context: dict | None = None
        if request.strategy_unit_id:
            su_repo = StrategyUnitRepository(self.session)
            su = await su_repo.get_by_id(request.strategy_unit_id)
            if su:
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
                    "knowledge_items": linked_ki or None,
                }

        existing_plans, _ = await self.repo.list(
            offer_id=request.offer_id,
            strategy_unit_id=request.strategy_unit_id,
            language=request.language,
            offset=0,
            limit=50,
        )
        existing_titles = [p.title for p in existing_plans if p.title]

        liked_plans = await self.repo.list_rated(request.offer_id, rating=1, limit=20)
        liked_topics = [{"title": p.title, "angle": p.angle} for p in liked_plans if p.title]
        disliked_plans = await self.repo.list_rated(request.offer_id, rating=-1, limit=20)
        disliked_topics = [{"title": p.title, "angle": p.angle} for p in disliked_plans if p.title]

        brand_voice = await ctx_service.resolve_brand_voice(request.offer_id)
        external_ctx = (request.external_context_text or "").strip() or None
        default_source_mode = "trend_bridge" if external_ctx else "kb"

        # Second heartbeat — context is built, we're about to call the LLM.
        # If the LLM connect hangs, this is the last event the client sees
        # before the (eventual) error, but at least the bar advanced.
        yield f"event: started\ndata: {json.dumps({'phase': 'calling_llm', 'kb_items': len(context_dict.get('knowledge_items', [])), 'trend_bridge': bool(external_ctx)})}\n\n"

        plans_emitted = 0
        # Captured once per stream when the hotspot event lands; copied
        # onto every plan row in this batch so each plan is self-contained
        # when later fetched by id (e.g. by script-writer via topic_plan_id).
        hotspot_for_db: dict | None = None
        try:
            async for event_type, payload in self.ai.generate_topic_plans_stream(
                offer_context=context_dict,
                count=request.count,
                channel=request.channel,
                language=request.language,
                strategy_unit_context=strategy_unit_context,
                existing_titles=existing_titles or None,
                liked_titles=liked_topics or None,
                disliked_titles=disliked_topics or None,
                user_instruction=request.instruction,
                brand_voice=brand_voice,
                external_context_text=external_ctx,
            ):
                if event_type == "thinking":
                    # Forward as-is. The frontend can show or hide it.
                    yield f"event: thinking\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                elif event_type == "hotspot":
                    if isinstance(payload, dict):
                        hotspot_for_db = payload
                    yield f"event: hotspot\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                elif event_type == "plan":
                    raw = payload
                    plan = await self.repo.create(
                        merchant_id=context.offer.merchant_id,
                        offer_id=request.offer_id,
                        source_mode=raw.get("source_mode", default_source_mode),
                        title=raw.get("title", ""),
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
                        # Trend-bridge persistence — see generate() for rationale.
                        hotspot_json=hotspot_for_db,
                        do_not_associate_json=raw.get("do_not_associate"),
                        relevance_tier=raw.get("relevance_tier"),
                        risk_of_forced_relevance=raw.get("risk_of_forced_relevance"),
                    )
                    # Commit per-plan so a streamed plan is durable the
                    # moment the user sees it — no risk of "card shown but
                    # row vanished if connection drops mid-stream".
                    await self.session.commit()
                    response_obj = TopicPlanResponse.model_validate(plan, from_attributes=True).model_dump(mode="json")
                    yield f"event: plan\ndata: {json.dumps(response_obj, ensure_ascii=False, default=str)}\n\n"
                    plans_emitted += 1
                elif event_type == "done":
                    yield f"event: done\ndata: {json.dumps({'count': plans_emitted, 'requested': request.count, 'trend_bridge': bool(external_ctx)})}\n\n"
        except Exception as e:
            logger.exception("Topic Studio stream failed")
            detail = str(e)
            resp = getattr(e, "response", None)
            if resp is not None:
                try:
                    body = resp.json()
                    err = body.get("error")
                    if isinstance(err, dict) and err.get("message"):
                        detail = err["message"]
                    else:
                        detail = body.get("message") or body.get("detail") or detail
                except Exception:
                    pass
            yield f"event: error\ndata: {json.dumps({'message': detail[:500]}, ensure_ascii=False)}\n\n"

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
