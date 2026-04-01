from __future__ import annotations

import io
import logging

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ai import get_ai_adapter, OpenAICompatibleAdapter, StubAIAdapter
from app.api.deps import get_db
from app.application.context_service import ContextService
from app.schemas.ai import (
    ExtractTextResponse,
    InferKnowledgeRequest,
    InferKnowledgeResponse,
    InferOfferKnowledgeRequest,
    InferOfferKnowledgeResponse,
    InferredKnowledgeItem,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/status")
async def ai_status(db: AsyncSession = Depends(get_db)):
    """Check whether a real LLM is configured and ready."""
    adapter = await get_ai_adapter(db, scene_key="knowledge")
    ready = not isinstance(adapter, StubAIAdapter)
    info = {}
    if isinstance(adapter, OpenAICompatibleAdapter):
        info = {"provider": adapter.provider, "model": adapter.model}
    return {"ready": ready, **info}


def _build_suggestions(raw: dict) -> dict[str, list[InferredKnowledgeItem]]:
    suggestions = {}
    for category, items in raw.items():
        suggestions[category] = [
            InferredKnowledgeItem(
                knowledge_type=category,
                title=item.get("title", ""),
                content_raw=item.get("content_raw", ""),
                confidence=item.get("confidence", 0.0),
            )
            for item in items
        ]
    return suggestions


@router.post("/infer-knowledge", response_model=InferKnowledgeResponse)
async def infer_knowledge(body: InferKnowledgeRequest, db: AsyncSession = Depends(get_db)):
    """Infer knowledge from an existing offer."""
    ctx_service = ContextService(db)
    context = await ctx_service.get_offer_context(body.offer_id)
    context_dict = context.model_dump(mode="json")

    adapter = await get_ai_adapter(db, scene_key="knowledge")
    if isinstance(adapter, StubAIAdapter):
        raise HTTPException(
            status_code=503,
            detail="NO_LLM_CONFIGURED",
        )
    try:
        raw = await adapter.infer_knowledge(context_dict, body.language, body.user_hint)
    except Exception as e:
        logger.error("LLM call failed: %s", e)
        raise HTTPException(status_code=502, detail=f"LLM_CALL_FAILED: {e}")

    return InferKnowledgeResponse(
        offer_id=body.offer_id,
        offer_name=context.offer.name,
        suggestions=_build_suggestions(raw),
    )


@router.post("/infer-offer-knowledge", response_model=InferOfferKnowledgeResponse)
async def infer_offer_knowledge(body: InferOfferKnowledgeRequest, db: AsyncSession = Depends(get_db)):
    """Infer knowledge from raw offer info (no offer_id needed).
    Used during offer creation before the offer exists in DB."""
    offer_data = {
        "offer": {
            "name": body.name,
            "offer_type": body.offer_type,
            "description": body.description,
        },
        "selling_points": [],
        "target_audiences": [],
        "target_scenarios": [],
        "knowledge_items": [],
    }

    adapter = await get_ai_adapter(db, scene_key="knowledge")
    if isinstance(adapter, StubAIAdapter):
        raise HTTPException(
            status_code=503,
            detail="NO_LLM_CONFIGURED",
        )
    try:
        raw = await adapter.infer_knowledge(offer_data, body.language)
    except Exception as e:
        logger.error("LLM call failed: %s", e)
        raise HTTPException(status_code=502, detail=f"LLM_CALL_FAILED: {e}")

    return InferOfferKnowledgeResponse(
        offer_name=body.name,
        suggestions=_build_suggestions(raw),
    )


def _extract_pdf_text(content: bytes) -> str:
    from PyPDF2 import PdfReader
    reader = PdfReader(io.BytesIO(content))
    parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    return "\n".join(parts)


async def _extract_url_text(url: str) -> str:
    """Extract text from a URL using Jina Reader (handles JS-rendered SPA pages)."""
    import re
    jina_url = f"https://r.jina.ai/{url}"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(jina_url, headers={"Accept": "text/plain"})
            resp.raise_for_status()
            text = resp.text.strip()
            if text:
                return text
    except Exception:
        logger.warning("Jina Reader failed for %s, falling back to direct fetch", url)

    # Fallback: direct fetch + strip tags
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except Exception as e:
        raise HTTPException(400, f"Unable to access URL: {e}")

    text = resp.content.decode("utf-8", errors="ignore")
    text = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", text)
    text = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_docx_text(content: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


@router.post("/extract-text", response_model=ExtractTextResponse)
async def extract_text(
    file: UploadFile | None = File(None),
    url: str | None = Form(None),
):
    """Extract text from a PDF/Word file or a URL."""
    if file and file.filename:
        content = await file.read()
        filename = file.filename.lower()

        if filename.endswith(".pdf"):
            text = _extract_pdf_text(content)
        elif filename.endswith((".docx", ".doc")):
            text = _extract_docx_text(content)
        elif filename.endswith(".txt"):
            text = content.decode("utf-8", errors="ignore")
        else:
            raise HTTPException(400, f"Unsupported file format: {file.filename}. Please upload a PDF, Word, or TXT file")

        if not text.strip():
            raise HTTPException(400, "Failed to extract text content from the file")

        return ExtractTextResponse(text=text.strip(), source="file", filename=file.filename)

    if url:
        # For PDF/Word URLs, fetch directly
        if url.lower().endswith((".pdf", ".docx", ".doc")):
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
            except Exception as e:
                raise HTTPException(400, f"Unable to access URL: {e}")

            if url.lower().endswith(".pdf"):
                text = _extract_pdf_text(resp.content)
            else:
                text = _extract_docx_text(resp.content)
        else:
            # Use Jina Reader to handle JS-rendered pages (Vue/React/SPA)
            text = await _extract_url_text(url)

        if not text.strip():
            raise HTTPException(400, "Failed to extract text content from the URL")

        return ExtractTextResponse(text=text.strip()[:10000], source="url")

    raise HTTPException(400, "Please provide a file or URL")
