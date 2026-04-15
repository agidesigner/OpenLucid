from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.merchants import router as merchants_router
from app.api.offers import router as offers_router
from app.api.knowledge import router as knowledge_router
from app.api.assets import router as assets_router
from app.api.topic_plans import router as topic_plans_router
from app.api.ai import router as ai_router
from app.api.strategy_units import router as strategy_units_router
from app.api.strategy_unit_links import router as strategy_unit_links_router
from app.api.setting import router as setting_router
from app.api.apps import router as apps_router
from app.api.brandkits import router as brandkits_router
from app.api.creations import router as creations_router
from app.api.feedback import router as feedback_router
from app.api.media_providers import router as media_providers_router
from app.api.videos import (
    creations_videos_router,
    videos_router,
)
from app.api.coverage import router_su as coverage_su_router, router_offer as coverage_offer_router, router_batch as coverage_batch_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(merchants_router)
api_router.include_router(coverage_batch_router)
api_router.include_router(offers_router)
api_router.include_router(knowledge_router)
api_router.include_router(assets_router)
api_router.include_router(topic_plans_router)
api_router.include_router(ai_router)
api_router.include_router(strategy_units_router)
api_router.include_router(strategy_unit_links_router)
api_router.include_router(setting_router)
api_router.include_router(coverage_su_router)
api_router.include_router(coverage_offer_router)
api_router.include_router(apps_router)
api_router.include_router(brandkits_router)
api_router.include_router(creations_router)
api_router.include_router(feedback_router)
api_router.include_router(media_providers_router)
api_router.include_router(creations_videos_router)
api_router.include_router(videos_router)
