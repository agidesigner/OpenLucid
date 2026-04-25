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
    MerchantCompletenessResponse,
    OfferCompletenessScore,
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

    async def get_batch_completeness_scores(
        self, merchant_id: UUID
    ) -> MerchantCompletenessResponse:
        """Company-level score = avg offer scores (0-85) + merchant brandkit (0-15).
        Per-offer score = profile(20) + knowledge(35) + strategy(15) + assets(15).
        Uses aggregate SQL — no N+1."""
        from sqlalchemy import text

        session = self.session

        # 1. Offer profile info
        rows = (await session.execute(text(
            "SELECT id, description, core_selling_points_json, "
            "target_audience_json, target_scenarios_json "
            "FROM offers WHERE merchant_id = :mid"
        ), {"mid": merchant_id})).fetchall()

        if not rows:
            return MerchantCompletenessResponse()

        offer_ids = [r[0] for r in rows]
        profile_data = {}
        # v1.1.9: also expose the offer-field "filled?" booleans to the
        # knowledge bucket below. selling_point / audience / scenario
        # have two valid storage sites (offer columns + knowledge_items
        # rows of matching type) — same dual-storage pattern v1.1.6
        # surfaced for the per-offer scorer. Pre-v1.1.9 the merchant
        # scorer's knowledge bucket only saw the KB-row site, so an
        # offer with all three populated as columns scored 0 on
        # selling+audience+scenario despite the data being right
        # there in the same SELECT.
        offer_fields_filled: dict[uuid.UUID, dict[str, bool]] = {}
        for r in rows:
            has_sp = bool(r[2] and isinstance(r[2], dict) and r[2].get("points"))
            has_au = bool(r[3] and isinstance(r[3], dict) and r[3].get("items"))
            has_sc = bool(r[4] and isinstance(r[4], dict) and r[4].get("items"))
            score = 0
            if r[1]:  # description
                score += 5
            if has_sp:
                score += 5
            if has_au:
                score += 5
            if has_sc:
                score += 5
            profile_data[r[0]] = score
            offer_fields_filled[r[0]] = {
                "selling_point": has_sp,
                "audience": has_au,
                "scenario": has_sc,
            }

        # 2. Knowledge by type per offer
        ki_rows = (await session.execute(text(
            "SELECT scope_id, knowledge_type, COUNT(*) "
            "FROM knowledge_items WHERE scope_type = 'offer' "
            "AND scope_id = ANY(:ids) GROUP BY scope_id, knowledge_type"
        ), {"ids": offer_ids})).fetchall()

        ki_data: dict[uuid.UUID, dict[str, int]] = {}
        for r in ki_rows:
            ki_data.setdefault(r[0], {})[r[1]] = r[2]

        # 3. Strategy unit count per offer
        su_rows = (await session.execute(text(
            "SELECT offer_id, COUNT(*) FROM strategy_units "
            "WHERE offer_id = ANY(:ids) GROUP BY offer_id"
        ), {"ids": offer_ids})).fetchall()
        su_data = {r[0]: r[1] for r in su_rows}

        # 4. Asset count per offer
        asset_rows = (await session.execute(text(
            "SELECT scope_id, COUNT(*) FROM assets "
            "WHERE scope_type = 'offer' AND scope_id = ANY(:ids) "
            "GROUP BY scope_id"
        ), {"ids": offer_ids})).fetchall()
        asset_data = {r[0]: r[1] for r in asset_rows}

        # 5. Merchant-level BrandKit (scope_type='merchant')
        # Scoring rubric for brandkit (max 15 points, unchanged):
        #   +8 — kit exists
        #   +4 — brand_voice text OR any color/font defined (textual or visual spec)
        #   +3 — at least one asset link (reference images / logo)
        bk_row = (await session.execute(text(
            "SELECT COUNT(DISTINCT bk.id) AS kit_count, "
            "BOOL_OR(NULLIF(TRIM(bk.brand_voice), '') IS NOT NULL) AS has_voice, "
            "COUNT(DISTINCT bc.id) AS color_count, "
            "COUNT(DISTINCT bf.id) AS font_count, "
            "COUNT(DISTINCT bal.id) AS asset_count "
            "FROM brandkits bk "
            "LEFT JOIN brandkit_asset_links bal ON bal.brandkit_id = bk.id "
            "LEFT JOIN brandkit_colors bc ON bc.brandkit_id = bk.id "
            "LEFT JOIN brandkit_fonts bf ON bf.brandkit_id = bk.id "
            "WHERE bk.scope_type = 'merchant' AND bk.scope_id = :mid"
        ), {"mid": merchant_id})).fetchone()

        brandkit_score = 0
        if bk_row and bk_row[0] > 0:
            brandkit_score += 8
            if bk_row[1] or bk_row[2] > 0 or bk_row[3] > 0:  # has_voice OR colors OR fonts
                brandkit_score += 4
            if bk_row[4] > 0:  # asset_count
                brandkit_score += 3

        # Compute per-offer scores (max 85)
        offer_scores: dict[str, OfferCompletenessScore] = {}
        for oid in offer_ids:
            profile = profile_data.get(oid, 0)

            ki_types = ki_data.get(oid, {})
            fields = offer_fields_filled.get(oid, {})
            knowledge = 0
            # v1.1.9: OR the offer-column site with the KB-row site
            # for selling_point / audience / scenario. Mirrors v1.1.6's
            # per-offer fix (CoverageService.get_offer_coverage). Two
            # offers built from the same source content — one via the
            # AI-infer path that writes KB rows, one via create_offer's
            # flat-list args that writes offer columns — should now
            # score the same 7+7+7 instead of 21 vs 0.
            if ki_types.get("selling_point", 0) > 0 or fields.get("selling_point"):
                knowledge += 7
            if ki_types.get("audience", 0) > 0 or fields.get("audience"):
                knowledge += 7
            if ki_types.get("scenario", 0) > 0 or fields.get("scenario"):
                knowledge += 7
            if ki_types.get("faq", 0) > 0:
                knowledge += 5
            if ki_types.get("objection", 0) > 0:
                knowledge += 5
            other_types = set(ki_types.keys()) - {"selling_point", "audience", "scenario", "faq", "objection"}
            if any(ki_types.get(t, 0) > 0 for t in other_types):
                knowledge += 4

            su_count = su_data.get(oid, 0)
            strategy = (10 if su_count >= 1 else 0) + (5 if su_count >= 2 else 0)

            a_count = asset_data.get(oid, 0)
            assets_score = (8 if a_count >= 1 else 0) + (4 if a_count >= 3 else 0) + (3 if a_count >= 5 else 0)

            total = profile + knowledge + strategy + assets_score

            if profile < 20:
                next_action = "add_description"
            elif knowledge < 21:
                next_action = "add_knowledge"
            elif strategy == 0:
                next_action = "create_strategy"
            elif assets_score == 0:
                next_action = "upload_assets"
            else:
                next_action = "done"

            offer_scores[str(oid)] = OfferCompletenessScore(
                total=total, profile=profile, knowledge=knowledge,
                strategy=strategy, assets=assets_score, next_action=next_action,
            )

        # Company total = avg offer scores (0-85) + brandkit (0-15)
        offer_avg = round(sum(s.total for s in offer_scores.values()) / len(offer_scores)) if offer_scores else 0
        company_total = offer_avg + brandkit_score

        # Company next action
        if not offer_scores or offer_avg < 20:
            company_next = "add_description"
        elif offer_avg < 50:
            company_next = "add_knowledge"
        elif brandkit_score == 0:
            company_next = "create_brandkit"
        else:
            # Find first non-done offer action
            company_next = next((s.next_action for s in offer_scores.values() if s.next_action != "done"), "done")

        return MerchantCompletenessResponse(
            company_total=company_total,
            brandkit=brandkit_score,
            offer_avg=offer_avg,
            next_action=company_next,
            offers=offer_scores,
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

        # selling_point / audience / scenario have TWO valid storage sites: the
        # offer's own ``core_selling_points_json`` / ``target_audience_json`` /
        # ``target_scenarios_json`` columns (written by ``create_offer``'s flat-
        # list args, rendered as the header tag chips) AND ``knowledge_items``
        # rows of the matching type (written by ``add_knowledge_item``). Either
        # source counts as fulfillment — pre-v1.1.6 the scorer only saw the KB
        # rows so a fully-populated offer read 0% ready.
        from app.models.offer import Offer
        offer = await self.session.get(Offer, offer_id)

        def _wrapped_has_items(payload: dict | None, key: str) -> bool:
            return bool(payload and isinstance(payload, dict) and payload.get(key))

        selling_filled = (
            knowledge_by_type.get("selling_point", 0) > 0
            or (offer is not None and _wrapped_has_items(offer.core_selling_points_json, "points"))
        )
        audience_filled = (
            knowledge_by_type.get("audience", 0) > 0
            or (offer is not None and _wrapped_has_items(offer.target_audience_json, "items"))
        )
        scenario_filled = (
            knowledge_by_type.get("scenario", 0) > 0
            or (offer is not None and _wrapped_has_items(offer.target_scenarios_json, "items"))
        )

        missing = []
        if not selling_filled:
            missing.append("selling_point")
        if not audience_filled:
            missing.append("audience")
        if not scenario_filled:
            missing.append("scenario")
        if asset_count == 0:
            missing.append("assets")
        if strategy_unit_count == 0:
            missing.append("strategy_units")

        total_checks = 5
        readiness_score = round((total_checks - len(missing)) / total_checks, 4)

        if not selling_filled:
            next_action = "add_knowledge"
        elif not audience_filled:
            next_action = "add_audience"
        elif not scenario_filled:
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
