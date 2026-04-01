import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationDep
from app.application.knowledge_service import KnowledgeService
from app.database import get_db
from app.schemas.common import PaginatedResponse
from app.schemas.knowledge import (
    KnowledgeBatchImport,
    KnowledgeBatchResult,
    KnowledgeBatchUpsertResult,
    KnowledgeItemCreate,
    KnowledgeItemResponse,
    KnowledgeItemUpdate,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("", response_model=KnowledgeItemResponse, status_code=201)
async def create_knowledge(data: KnowledgeItemCreate, db: AsyncSession = Depends(get_db)):
    svc = KnowledgeService(db)
    return await svc.create(data)


@router.post("/batch", response_model=KnowledgeBatchResult, status_code=201)
async def batch_import_knowledge(data: KnowledgeBatchImport, db: AsyncSession = Depends(get_db)):
    svc = KnowledgeService(db)
    # Override scope on each item to match batch-level scope
    items = []
    for item in data.items:
        item.scope_type = data.scope_type
        item.scope_id = data.scope_id
        items.append(item)
    created = await svc.batch_create(items)
    return KnowledgeBatchResult(created=len(created), items=created)


@router.post("/batch-upsert", response_model=KnowledgeBatchUpsertResult, status_code=200)
async def batch_upsert_knowledge(data: KnowledgeBatchImport, db: AsyncSession = Depends(get_db)):
    svc = KnowledgeService(db)
    items = []
    for item in data.items:
        item.scope_type = data.scope_type
        item.scope_id = data.scope_id
        items.append(item)
    return await svc.batch_upsert(items)


@router.get("", response_model=PaginatedResponse[KnowledgeItemResponse])
async def list_knowledge(
    pagination: PaginationDep,
    scope_type: str | None = Query(None),
    scope_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    svc = KnowledgeService(db)
    items, total = await svc.list(scope_type=scope_type, scope_id=scope_id, **pagination)
    return PaginatedResponse(items=items, total=total, **pagination)


@router.get("/{knowledge_id}", response_model=KnowledgeItemResponse)
async def get_knowledge(knowledge_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = KnowledgeService(db)
    return await svc.get(knowledge_id)


@router.patch("/{knowledge_id}", response_model=KnowledgeItemResponse)
async def update_knowledge(
    knowledge_id: uuid.UUID, data: KnowledgeItemUpdate, db: AsyncSession = Depends(get_db)
):
    svc = KnowledgeService(db)
    return await svc.update(knowledge_id, data)


@router.delete("/{knowledge_id}", status_code=204)
async def delete_knowledge(knowledge_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = KnowledgeService(db)
    await svc.delete(knowledge_id)
