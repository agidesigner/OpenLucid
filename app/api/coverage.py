import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.coverage_service import CoverageService
from app.application.topic_plan_service import TopicPlanService
from app.database import get_db
from app.schemas.coverage import (
    MerchantCompletenessResponse,
    OfferCompletenessScore,
    OfferCoverageReview,
    RecommendedAssetsResponse,
    RecommendedKnowledgeResponse,
    StrategyUnitCoverageReview,
    StrategyUnitGenerateTopicsRequest,
)
from app.schemas.topic_plan import TopicPlanGenerateRequest, TopicPlanGenerateResponse

router_su = APIRouter(prefix="/strategy-units/{unit_id}", tags=["coverage"])
router_offer = APIRouter(prefix="/offers/{offer_id}", tags=["coverage"])
router_batch = APIRouter(prefix="/offers", tags=["coverage"])


@router_batch.get("/completeness-scores", response_model=MerchantCompletenessResponse)
async def get_completeness_scores(
    merchant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    svc = CoverageService(db)
    return await svc.get_batch_completeness_scores(merchant_id)


@router_su.get("/review-coverage", response_model=StrategyUnitCoverageReview)
async def review_unit_coverage(unit_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = CoverageService(db)
    return await svc.get_unit_coverage(unit_id)


@router_su.get("/recommended-knowledge", response_model=RecommendedKnowledgeResponse)
async def get_recommended_knowledge(unit_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = CoverageService(db)
    return await svc.get_recommended_knowledge(unit_id)


@router_su.get("/recommended-assets", response_model=RecommendedAssetsResponse)
async def get_recommended_assets(unit_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = CoverageService(db)
    return await svc.get_recommended_assets(unit_id)


@router_su.post("/generate-topics", response_model=TopicPlanGenerateResponse, status_code=201)
async def generate_topics_for_unit(
    unit_id: uuid.UUID,
    data: StrategyUnitGenerateTopicsRequest,
    db: AsyncSession = Depends(get_db),
):
    cov_svc = CoverageService(db)
    coverage = await cov_svc.get_unit_coverage(unit_id)

    request = TopicPlanGenerateRequest(
        offer_id=coverage.offer_id,
        strategy_unit_id=unit_id,
        channel=data.channel,
        language=data.language,
        count=data.count,
    )
    svc = TopicPlanService(db)
    plans, thinking, hotspot = await svc.generate(request)
    return TopicPlanGenerateResponse(
        offer_id=coverage.offer_id,
        count=len(plans),
        plans=plans,
        thinking=thinking,
        hotspot=hotspot,
    )


@router_offer.get("/coverage-review", response_model=OfferCoverageReview)
async def review_offer_coverage(offer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = CoverageService(db)
    return await svc.get_offer_coverage(offer_id)
