import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from sqlalchemy import text

from app.api.deps import PaginationDep
from app.application.context_service import ContextService
from app.application.knowledge_inference_service import KnowledgeInferenceService
from app.application.offer_service import OfferService
from app.database import get_db
from app.libs.lang_detect import cjk_ratio
from app.models.knowledge_item import KnowledgeItem
from app.schemas.common import PaginatedResponse
from app.schemas.context import OfferContextSummary
from app.schemas.knowledge_inference import KnowledgeInferenceReport
from app.schemas.offer import OfferCreate, OfferResponse, OfferUpdate

router = APIRouter(prefix="/offers", tags=["offers"])


@router.post("", response_model=OfferResponse, status_code=201)
async def create_offer(data: OfferCreate, db: AsyncSession = Depends(get_db)):
    svc = OfferService(db)
    return await svc.create(data)


@router.get("/logos")
async def get_offer_logos(
    merchant_id: uuid.UUID | None = Query(None),
    offer_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Resolve primary brandkit logo URL + native dimensions for one
    offer (``?offer_id=``) or every offer under a merchant
    (``?merchant_id=``) — same response shape, same DISTINCT ON query.

    Used by:
    - index.html (Library card grid) — passes ``merchant_id``
    - offer.html (detail page header) — passes ``offer_id``

    Both pages need the same ``{url, w, h}`` payload to size the logo
    container by aspect ratio (square logos render in a 40×40 frame,
    wide wordmarks scale up to 80×40). Sharing one endpoint avoids
    a second copy of the same JOIN/DISTINCT-ON logic.

    Returns ``{offer_id_str: {url, w, h}}`` — w/h come from the
    asset's pre-extracted ``metadata_json`` (recorded at upload time
    by the asset pipeline). Defaults to 0 if metadata is missing,
    in which case the frontend falls back to the 40×40 square frame.
    Offers without any logo are simply omitted; callers fall through
    to whatever placeholder the page uses (letter avatar in the
    Library, type-emoji in the detail page).
    """
    sql = """
        SELECT DISTINCT ON (bk.scope_id)
               bk.scope_id::text AS offer_id,
               a.id::text AS asset_id,
               a.metadata_json AS meta
        FROM brandkits bk
        JOIN brandkit_asset_links bal ON bal.brandkit_id = bk.id
        JOIN assets a ON a.id = bal.asset_id
        JOIN offers o ON o.id = bk.scope_id
        WHERE bk.scope_type = 'offer'
          AND bal.role = 'logo'
          AND a.asset_type = 'image'
    """
    params: dict = {}
    if merchant_id is not None:
        sql += " AND o.merchant_id = :mid"
        params["mid"] = merchant_id
    if offer_id is not None:
        sql += " AND bk.scope_id = :oid"
        params["oid"] = offer_id
    sql += " ORDER BY bk.scope_id, bal.priority ASC, bal.created_at ASC"

    rows = (await db.execute(text(sql), params)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        meta = r.meta or {}
        out[r.offer_id] = {
            "url": f"/api/v1/assets/{r.asset_id}/file",
            "w": int(meta.get("width") or 0),
            "h": int(meta.get("height") or 0),
        }
    return out


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
    if CJK-char ratio >= 30%, else "en". Falls back to offer.locale if text is empty.
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

    ratio = cjk_ratio(sample)
    lang = "zh" if ratio >= 0.3 else "en"
    return {"language": lang, "source": "content_sample", "cjk_ratio": round(ratio, 3)}


@router.get("/{offer_id}/consumption_summary")
async def get_offer_consumption_summary(
    offer_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Return aggregate stats on how this offer's knowledge has been consumed.

    Shape: {creations_total, by_source: {<source_app>: count}, last_used_at}.
    Used by the Offer page's Consumption card.
    """
    svc = OfferService(db)
    return await svc.get_consumption_summary(offer_id)


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


class _InferKnowledgeBody(BaseModel):
    """Request body for ``POST /offers/{id}/infer-knowledge``.

    Both fields are optional: when ``language`` is omitted, the
    service defaults to the offer's own locale (or ``zh-CN``).
    ``user_hint`` is an extra free-text prompt that the LLM will
    consider alongside the offer's existing fields — useful when an
    operator wants to nudge the inference ("focus on B2B angles").
    """
    language: str | None = None
    user_hint: str | None = None


@router.post("/{offer_id}/infer-knowledge", response_model=KnowledgeInferenceReport)
async def infer_offer_knowledge(
    offer_id: uuid.UUID,
    body: _InferKnowledgeBody | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Run the knowledge-scene LLM against this offer + persist
    the suggestions as knowledge_items rows with ``source_type=
    ai_inferred``.

    v1.2.0 — corresponds to the MCP ``infer_knowledge_for_offer``
    tool and the CLI ``infer-knowledge-for-offer`` command. This is
    the auto-persist path; the WebUI create-wizard's preview path
    (``/ai/infer-offer-knowledge-stream``) keeps the suggestions-
    for-user-review behaviour and is unchanged.

    Errors from the LLM (timeout, auth, rate limit) are returned in
    the response body as ``{success: false, reason: ...}`` rather
    than raised — so a partial-failure from re-inferring an existing
    offer doesn't 500 and lose the offer's unchanged data."""
    body = body or _InferKnowledgeBody()
    svc = KnowledgeInferenceService(db)
    return await svc.infer_and_persist_offer_knowledge(
        offer_id,
        language=body.language,
        user_hint=body.user_hint,
        trigger="manual:rest_endpoint",
    )
