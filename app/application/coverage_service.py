from __future__ import annotations

import uuid
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError
from app.infrastructure.asset_repo import AssetRepository
from app.infrastructure.knowledge_repo import KnowledgeItemRepository
from app.infrastructure.strategy_unit_link_repo import (
    StrategyUnitAssetLinkRepository,
    StrategyUnitKnowledgeLinkRepository,
)
from app.infrastructure.strategy_unit_repo import StrategyUnitRepository
from app.infrastructure.topic_plan_repo import TopicPlanRepository
from app.schemas.coverage import (
    OfferCoverageReview,
    RecommendedAssetsResponse,
    RecommendedKnowledgeResponse,
    StrategyUnitCoverageReview,
)


class CoverageService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.ki_repo = KnowledgeItemRepository(session)
        self.asset_repo = AssetRepository(session)
        self.su_repo = StrategyUnitRepository(session)
        self.tp_repo = TopicPlanRepository(session)
        self.ki_link_repo = StrategyUnitKnowledgeLinkRepository(session)
        self.asset_link_repo = StrategyUnitAssetLinkRepository(session)

    async def get_unit_coverage(self, unit_id: UUID) -> StrategyUnitCoverageReview:
        unit = await self.su_repo.get_by_id(unit_id)
        if not unit:
            raise NotFoundError("StrategyUnit", str(unit_id))

        offer_id = unit.offer_id

        _, total_ki = await self.ki_repo.list(scope_type="offer", scope_id=offer_id, offset=0, limit=1)
        _, total_assets = await self.asset_repo.list(scope_type="offer", scope_id=offer_id, offset=0, limit=1)

        _, linked_ki_count = await self.ki_link_repo.list_by_strategy_unit(unit_id, offset=0, limit=1)
        _, linked_asset_count = await self.asset_link_repo.list_by_strategy_unit(unit_id, offset=0, limit=1)

        topic_count = await self.tp_repo.count_by_strategy_unit(unit_id)

        ki_coverage = (linked_ki_count / total_ki) if total_ki > 0 else 0.0
        asset_coverage = (linked_asset_count / total_assets) if total_assets > 0 else 0.0

        if linked_ki_count == 0:
            next_action = "link_knowledge"
        elif linked_asset_count == 0:
            next_action = "link_assets"
        elif topic_count == 0:
            next_action = "generate_topics"
        else:
            next_action = "done"
        next_action_label = next_action  # label resolved by frontend i18n

        is_ready = linked_ki_count > 0

        return StrategyUnitCoverageReview(
            unit_id=unit_id,
            offer_id=offer_id,
            total_offer_knowledge=total_ki,
            linked_knowledge=linked_ki_count,
            knowledge_coverage=round(ki_coverage, 4),
            total_offer_assets=total_assets,
            linked_assets=linked_asset_count,
            asset_coverage=round(asset_coverage, 4),
            topic_count=topic_count,
            next_action=next_action,
            next_action_label=next_action_label,
            is_ready_to_generate=is_ready,
        )

    async def get_recommended_knowledge(self, unit_id: UUID) -> RecommendedKnowledgeResponse:
        unit = await self.su_repo.get_by_id(unit_id)
        if not unit:
            raise NotFoundError("StrategyUnit", str(unit_id))

        offer_id = unit.offer_id
        links, _ = await self.ki_link_repo.list_by_strategy_unit(unit_id, offset=0, limit=500)
        linked_ids = {lnk.knowledge_item_id for lnk in links}

        all_ki, _ = await self.ki_repo.list(scope_type="offer", scope_id=offer_id, offset=0, limit=500)
        unlinked = [ki for ki in all_ki if ki.id not in linked_ids]

        return RecommendedKnowledgeResponse(
            unit_id=unit_id,
            offer_id=offer_id,
            items=unlinked,
            total=len(unlinked),
        )

    async def get_recommended_assets(self, unit_id: UUID) -> RecommendedAssetsResponse:
        unit = await self.su_repo.get_by_id(unit_id)
        if not unit:
            raise NotFoundError("StrategyUnit", str(unit_id))

        offer_id = unit.offer_id
        links, _ = await self.asset_link_repo.list_by_strategy_unit(unit_id, offset=0, limit=500)
        linked_ids = {lnk.asset_id for lnk in links}

        all_assets, _ = await self.asset_repo.list(scope_type="offer", scope_id=offer_id, offset=0, limit=500)
        unlinked = [a for a in all_assets if a.id not in linked_ids]

        return RecommendedAssetsResponse(
            unit_id=unit_id,
            offer_id=offer_id,
            items=unlinked,
            total=len(unlinked),
        )

    async def get_offer_coverage(self, offer_id: UUID) -> OfferCoverageReview:
        all_ki, knowledge_count = await self.ki_repo.list(scope_type="offer", scope_id=offer_id, offset=0, limit=500)
        all_assets, asset_count = await self.asset_repo.list(scope_type="offer", scope_id=offer_id, offset=0, limit=500)

        knowledge_by_type: dict[str, int] = {}
        for ki in all_ki:
            knowledge_by_type[ki.knowledge_type] = knowledge_by_type.get(ki.knowledge_type, 0) + 1

        asset_by_type: dict[str, int] = {}
        for a in all_assets:
            asset_by_type[a.asset_type] = asset_by_type.get(a.asset_type, 0) + 1

        strategy_unit_count = await self.su_repo.count_by_offer(offer_id)
        topic_count = await self.tp_repo.count_by_offer(offer_id)

        required_types = {"selling_point", "audience", "scenario"}
        missing = []
        for t in required_types:
            if knowledge_by_type.get(t, 0) == 0:
                missing.append(t)
        if asset_count == 0:
            missing.append("assets")
        if strategy_unit_count == 0:
            missing.append("strategy_units")

        total_checks = 5
        readiness_score = round((total_checks - len(missing)) / total_checks, 4)

        if knowledge_by_type.get("selling_point", 0) == 0:
            next_action = "add_knowledge"
        elif knowledge_by_type.get("audience", 0) == 0:
            next_action = "add_audience"
        elif knowledge_by_type.get("scenario", 0) == 0:
            next_action = "add_scenario"
        elif asset_count == 0:
            next_action = "upload_assets"
        elif strategy_unit_count == 0:
            next_action = "create_strategy_unit"
        else:
            next_action = "generate_topics"
        next_action_label = next_action  # label resolved by frontend i18n

        return OfferCoverageReview(
            offer_id=offer_id,
            knowledge_count=knowledge_count,
            knowledge_by_type=knowledge_by_type,
            asset_count=asset_count,
            asset_by_type=asset_by_type,
            strategy_unit_count=strategy_unit_count,
            topic_count=topic_count,
            missing=missing,
            readiness_score=readiness_score,
            next_action=next_action,
            next_action_label=next_action_label,
        )
