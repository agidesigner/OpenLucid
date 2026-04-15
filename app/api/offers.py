import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationDep
from app.application.context_service import ContextService
from app.application.offer_service import OfferService
from app.database import get_db
from app.models.knowledge_item import KnowledgeItem
from app.schemas.common import PaginatedResponse
from app.schemas.context import OfferContextSummary
from app.schemas.offer import OfferCreate, OfferResponse, OfferUpdate

router = APIRouter(prefix="/offers", tags=["offers"])


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    total = 0
    cjk = 0
    for ch in text:
        if ch.isspace():
            continue
        total += 1
        # CJK Unified Ideographs + common CJK symbols
        if "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf":
            cjk += 1
    return (cjk / total) if total else 0.0


@router.post("", response_model=OfferResponse, status_code=201)
async def create_offer(data: OfferCreate, db: AsyncSession = Depends(get_db)):
    svc = OfferService(db)
    return await svc.create(data)


@router.get("", response_model=PaginatedResponse[OfferResponse])
async def list_offers(
    pagination: PaginationDep,
    merchant_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    svc = OfferService(db)
    items, total = await svc.list(merchant_id=merchant_id, **pagination)
    return PaginatedResponse(items=items, total=total, **pagination)


@router.get("/{offer_id}", response_model=OfferResponse)
async def get_offer(offer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = OfferService(db)
    return await svc.get(offer_id)


@router.get("/{offer_id}/context", response_model=OfferContextSummary)
async def get_offer_context(offer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = ContextService(db)
    return await svc.get_offer_context(offer_id)


@router.get("/{offer_id}/primary_lang")
async def get_offer_primary_lang(offer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Guess whether this offer's content is primarily Chinese or English.

    Used by script-writer/content-studio to default the output-language picker.
    Samples offer name + description + first 10 knowledge items; returns "zh"
    if CJK-char ratio ≥ 30%, else "en". Falls back to offer.locale if text is empty.
    """
    svc = OfferService(db)
    offer = await svc.get(offer_id)

    parts: list[str] = [offer.name or "", offer.description or "", offer.positioning or ""]
    stmt = (
        select(KnowledgeItem.title, KnowledgeItem.content_raw)
        .where(KnowledgeItem.scope_type == "offer", KnowledgeItem.scope_id == offer_id)
        .limit(10)
    )
    for title, content_raw in (await db.execute(stmt)).all():
        if title:
            parts.append(title)
        if content_raw:
            parts.append(content_raw[:500])

    sample = "\n".join(p for p in parts if p).strip()
    if not sample:
        lang = "zh" if (offer.locale or "").startswith("zh") else "en"
        return {"language": lang, "source": "offer_locale"}

    ratio = _cjk_ratio(sample)
    lang = "zh" if ratio >= 0.3 else "en"
    return {"language": lang, "source": "content_sample", "cjk_ratio": round(ratio, 3)}


@router.patch("/{offer_id}", response_model=OfferResponse)
async def update_offer(
    offer_id: uuid.UUID, data: OfferUpdate, db: AsyncSession = Depends(get_db)
):
    svc = OfferService(db)
    return await svc.update(offer_id, data)


@router.delete("/{offer_id}", status_code=204)
async def delete_offer(offer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = OfferService(db)
    await svc.delete(offer_id)
