import logging
import uuid

from sqlalchemy import delete as sql_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError
from app.infrastructure.merchant_repo import MerchantRepository
from app.infrastructure.offer_repo import OfferRepository
from app.models.asset import Asset
from app.models.brandkit import BrandKit
from app.models.creation import Creation
from app.models.knowledge_item import KnowledgeItem
from app.models.offer import Offer
from app.models.topic_plan import TopicPlan
from app.schemas.offer import OfferCreate, OfferUpdate

logger = logging.getLogger(__name__)


class OfferService:
    def __init__(self, session: AsyncSession):
        self.repo = OfferRepository(session)
        self.merchant_repo = MerchantRepository(session)
        self.session = session

    async def create(self, data: OfferCreate) -> Offer:
        # NOTE: ``offer_model`` (the derived 6-class enum) is **not** inferred
        # synchronously here. Doing so used to add a 0.5–1s LLM round-trip on
        # every create, which the wizard pays end-to-end. We now schedule
        # ``infer_offer_model_in_background`` from the route handler via
        # FastAPI ``BackgroundTasks`` so the response returns as soon as the
        # row is persisted; the model column is back-filled within ~1s.
        merchant = await self.merchant_repo.get_by_id(data.merchant_id)
        if not merchant:
            raise NotFoundError("Merchant", str(data.merchant_id))
        offer = await self.repo.create(**data.model_dump())
        return offer


    async def get(self, offer_id: uuid.UUID) -> Offer:
        offer = await self.repo.get_by_id(offer_id)
        if not offer:
            raise NotFoundError("Offer", str(offer_id))
        return offer

    async def list(
        self, merchant_id: uuid.UUID | None = None, page: int = 1, page_size: int = 20
    ) -> tuple[list[Offer], int]:
        offset = (page - 1) * page_size
        return await self.repo.list(merchant_id=merchant_id, offset=offset, limit=page_size)

    async def delete(self, offer_id: uuid.UUID) -> None:
        """Hard-delete an offer and all its dependent rows.

        `knowledge_items`, `brandkits`, and `assets` use a polymorphic
        (scope_type, scope_id) pointer rather than a real FK, so SQL-level
        cascades don't fire. `topic_plans` and `creations` have an `offer_id`
        column but no cascade was declared. Before v0.9.9.4 this left 284+
        orphan knowledge_items across 13 deleted offers in production.

        We explicitly clean each dependent table here. We do *not* currently
        delete asset files from disk during cascade — asset rows for
        offer-scope assets are rare (2 in prod history) and the disk bytes
        are cheaper to leak than to engineer a safe file-cleanup path
        through multiple services. Assets with merchant scope are untouched.
        """
        offer = await self.get(offer_id)
        session = self.repo.session

        # Order matters: delete pointing-in rows first, then the offer
        await session.execute(sql_delete(TopicPlan).where(TopicPlan.offer_id == offer_id))
        await session.execute(sql_delete(Creation).where(Creation.offer_id == offer_id))
        await session.execute(sql_delete(KnowledgeItem).where(
            KnowledgeItem.scope_type == "offer",
            KnowledgeItem.scope_id == offer_id,
        ))
        await session.execute(sql_delete(BrandKit).where(
            BrandKit.scope_type == "offer",
            BrandKit.scope_id == offer_id,
        ))
        await session.execute(sql_delete(Asset).where(
            Asset.scope_type == "offer",
            Asset.scope_id == offer_id,
        ))
        # strategy_units has a real FK to offers.id and cascades via SQL

        await self.repo.delete(offer)

    async def update(self, offer_id: uuid.UUID, data: OfferUpdate) -> Offer:
        offer = await self.get(offer_id)
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return offer
        return await self.repo.update(offer, **update_data)

    async def get_consumption_summary(self, offer_id: uuid.UUID) -> dict:
        """How has this offer's knowledge been consumed?

        Aggregates Creation rows linked to the offer — total count, breakdown
        by source_app, and last-used timestamp. Feeds the Offer page's
        "Consumption" card so users can see whether agents / apps are using
        the world they've built.
        """
        await self.get(offer_id)  # 404 if offer missing

        stmt = (
            select(Creation.source_app, func.count(Creation.id), func.max(Creation.created_at))
            .where(Creation.offer_id == offer_id)
            .group_by(Creation.source_app)
        )
        rows = (await self.session.execute(stmt)).all()

        by_source: dict[str, int] = {}
        total = 0
        last_used_at = None
        for source_app, count, max_created in rows:
            by_source[source_app or "unknown"] = int(count)
            total += int(count)
            if max_created is not None and (last_used_at is None or max_created > last_used_at):
                last_used_at = max_created

        return {
            "creations_total": total,
            "by_source": by_source,
            "last_used_at": last_used_at.isoformat() if last_used_at else None,
        }


async def infer_offer_model_in_background(offer_id: uuid.UUID) -> None:
    """Run the ``offer_model`` LLM classifier with its own DB session.

    Designed for FastAPI ``BackgroundTasks`` — the request session is
    already closed by the time we run, so we open a fresh one. Errors are
    swallowed (logged warn) on purpose: the offer row is already correct
    without ``offer_model``, this is purely a derived column.
    """
    from app.adapters.ai import get_ai_adapter
    from app.database import async_session_factory

    async with async_session_factory() as session:
        try:
            offer = await session.get(Offer, offer_id)
            if offer is None:
                logger.warning("offer_model bg: offer %s vanished before inference", offer_id)
                return
            ai = await get_ai_adapter(session, scene_key="offer_model")
            model = await ai.infer_offer_model(
                name=offer.name,
                description=offer.description or "",
                offer_type=offer.offer_type,
            )
            offer.offer_model = model
            await session.commit()
            logger.info("Inferred offer_model='%s' for offer '%s' (bg)", model, offer.name)
        except Exception:
            logger.warning(
                "Failed to infer offer_model for offer %s (bg)", offer_id, exc_info=True,
            )
