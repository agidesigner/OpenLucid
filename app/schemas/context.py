import uuid
from typing import Any

from pydantic import BaseModel

from app.schemas.asset import AssetResponse
from app.schemas.knowledge import KnowledgeItemResponse
from app.schemas.merchant import MerchantResponse
from app.schemas.offer import OfferResponse


class AssetSummary(BaseModel):
    total: int
    by_type: dict[str, int]
    parsed: int
    unparsed: int


class KnowledgeSummary(BaseModel):
    total: int
    by_type: dict[str, int]


class OfferContextSummary(BaseModel):
    """Aggregated context for an Offer, combining merchant + offer level data.
    Designed to be fed into AI for topic generation."""

    offer: OfferResponse
    merchant: MerchantResponse

    # Knowledge
    merchant_knowledge: KnowledgeSummary
    offer_knowledge: KnowledgeSummary
    knowledge_items: list[KnowledgeItemResponse]

    # Assets
    merchant_assets: AssetSummary
    offer_assets: AssetSummary
    assets: list[AssetResponse]

    # Derived context for AI consumption
    selling_points: list[str]
    target_audiences: list[str]
    target_scenarios: list[str]
    available_proof_assets: int
