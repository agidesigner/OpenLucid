from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError
from app.infrastructure.asset_repo import AssetRepository
from app.infrastructure.brandkit_repo import BrandKitRepository
from app.infrastructure.knowledge_repo import KnowledgeItemRepository
from app.infrastructure.merchant_repo import MerchantRepository
from app.infrastructure.offer_repo import OfferRepository
from app.schemas.context import (
    AssetSummary,
    KnowledgeSummary,
    OfferContextSummary,
)


class ContextService:
    """Aggregates merchant + offer level knowledge and assets into a unified context."""

    def __init__(self, session: AsyncSession):
        self.merchant_repo = MerchantRepository(session)
        self.offer_repo = OfferRepository(session)
        self.knowledge_repo = KnowledgeItemRepository(session)
        self.asset_repo = AssetRepository(session)
        self.brandkit_repo = BrandKitRepository(session)

    async def resolve_brand_voice(self, offer_id: uuid.UUID) -> str | None:
        """Look up the brand_voice string that should overlay content generation
        for this offer. Prefers the offer-scoped brandkit; falls back to the
        merchant-scoped kit. Returns ``None`` when no kit has one — callers
        (e.g. script_composer) omit the BRAND layer entirely in that case.
        """
        offer = await self.offer_repo.get_by_id(offer_id)
        if not offer:
            return None
        offer_kit = await self.brandkit_repo.get_by_scope("offer", offer_id)
        if offer_kit and (offer_kit.brand_voice or "").strip():
            return offer_kit.brand_voice
        merchant_kit = await self.brandkit_repo.get_by_scope("merchant", offer.merchant_id)
        if merchant_kit and (merchant_kit.brand_voice or "").strip():
            return merchant_kit.brand_voice
        return None

    async def get_offer_context(self, offer_id: uuid.UUID) -> OfferContextSummary:
        # Load offer + merchant
        offer = await self.offer_repo.get_by_id(offer_id)
        if not offer:
            raise NotFoundError("Offer", str(offer_id))

        merchant = await self.merchant_repo.get_by_id(offer.merchant_id)
        if not merchant:
            raise NotFoundError("Merchant", str(offer.merchant_id))

        # Load knowledge: merchant-level + offer-level
        merchant_knowledge_items, merchant_k_total = await self.knowledge_repo.list(
            scope_type="merchant", scope_id=offer.merchant_id, offset=0, limit=500
        )
        offer_knowledge_items, offer_k_total = await self.knowledge_repo.list(
            scope_type="offer", scope_id=offer_id, offset=0, limit=500
        )
        all_knowledge = merchant_knowledge_items + offer_knowledge_items

        # Load assets: merchant-level + offer-level
        merchant_assets_list, merchant_a_total = await self.asset_repo.list(
            scope_type="merchant", scope_id=offer.merchant_id, offset=0, limit=500
        )
        offer_assets_list, offer_a_total = await self.asset_repo.list(
            scope_type="offer", scope_id=offer_id, offset=0, limit=500
        )
        all_assets = merchant_assets_list + offer_assets_list

        # Build summaries
        merchant_k_summary = self._build_knowledge_summary(merchant_knowledge_items, merchant_k_total)
        offer_k_summary = self._build_knowledge_summary(offer_knowledge_items, offer_k_total)
        merchant_a_summary = self._build_asset_summary(merchant_assets_list, merchant_a_total)
        offer_a_summary = self._build_asset_summary(offer_assets_list, offer_a_total)

        # Extract derived context from offer fields
        selling_points = self._extract_list(offer.core_selling_points_json)
        target_audiences = self._extract_list(offer.target_audience_json)
        target_scenarios = self._extract_list(offer.target_scenarios_json)

        # Count proof-type assets (tagged or knowledge-derived)
        proof_count = sum(
            1 for a in all_assets
            if a.tags_json and "proof" in str(a.tags_json)
        )

        return OfferContextSummary(
            offer=offer,
            merchant=merchant,
            merchant_knowledge=merchant_k_summary,
            offer_knowledge=offer_k_summary,
            knowledge_items=all_knowledge,
            merchant_assets=merchant_a_summary,
            offer_assets=offer_a_summary,
            assets=all_assets,
            selling_points=selling_points,
            target_audiences=target_audiences,
            target_scenarios=target_scenarios,
            available_proof_assets=proof_count,
        )

    @staticmethod
    def _build_knowledge_summary(items: list, total: int) -> KnowledgeSummary:
        by_type: dict[str, int] = Counter(item.knowledge_type for item in items)
        return KnowledgeSummary(total=total, by_type=dict(by_type))

    @staticmethod
    def _build_asset_summary(items: list, total: int) -> AssetSummary:
        by_type: dict[str, int] = Counter(item.asset_type for item in items)
        parsed = sum(1 for item in items if item.parse_status == "done")
        return AssetSummary(
            total=total,
            by_type=dict(by_type),
            parsed=parsed,
            unparsed=total - parsed,
        )

    @staticmethod
    def _extract_list(json_field: dict | list | None) -> list[str]:
        """Extract a flat list of strings from a JSON field (dict or list)."""
        if json_field is None:
            return []
        if isinstance(json_field, list):
            return [str(x) for x in json_field]
        if isinstance(json_field, dict):
            # Support {"points": [...]} or {"items": [...]} patterns
            for key in ("points", "items", "list", "values"):
                if key in json_field and isinstance(json_field[key], list):
                    return [str(x) for x in json_field[key]]
            # Fallback: return values if they're strings
            return [str(v) for v in json_field.values() if isinstance(v, str)]
        return []
