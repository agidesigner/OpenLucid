from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_trace import LLMTrace
from app.schemas.setting import (
    LLMTraceClearResponse,
    LLMTraceDetailResponse,
    LLMTraceListItem,
    LLMTraceListResponse,
    LLMTraceStatsResponse,
)


def _preview(text: str | None, limit: int = 240) -> str | None:
    if not text:
        return None
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _apply_filters(
    stmt,
    *,
    scene_key: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    query: str | None = None,
):
    if scene_key:
        if scene_key == "__default__":
            stmt = stmt.where(LLMTrace.scene_key.is_(None))
        else:
            stmt = stmt.where(LLMTrace.scene_key == scene_key)
    if status:
        stmt = stmt.where(LLMTrace.status == status)
    if since:
        stmt = stmt.where(LLMTrace.created_at >= since)
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                LLMTrace.system_prompt.ilike(like),
                LLMTrace.user_prompt.ilike(like),
                LLMTrace.response_text.ilike(like),
                LLMTrace.thinking_text.ilike(like),
                LLMTrace.error_message.ilike(like),
            )
        )
    return stmt


def _to_list_item(trace: LLMTrace) -> LLMTraceListItem:
    return LLMTraceListItem(
        id=str(trace.id),
        scene_key=trace.scene_key,
        call_type=trace.call_type,
        model_name=trace.model_name,
        provider=trace.provider,
        status=trace.status,
        latency_ms=trace.latency_ms,
        prompt_tokens=trace.prompt_tokens,
        completion_tokens=trace.completion_tokens,
        total_tokens=trace.total_tokens,
        created_at=trace.created_at.isoformat(),
        finished_at=trace.finished_at.isoformat() if trace.finished_at else None,
        system_prompt_preview=_preview(trace.system_prompt),
        user_prompt_preview=_preview(trace.user_prompt),
        response_preview=_preview(trace.response_text),
        thinking_preview=_preview(trace.thinking_text),
    )


def _to_detail(trace: LLMTrace) -> LLMTraceDetailResponse:
    return LLMTraceDetailResponse(
        id=str(trace.id),
        scene_key=trace.scene_key,
        call_type=trace.call_type,
        model_name=trace.model_name,
        provider=trace.provider,
        status=trace.status,
        error_message=trace.error_message,
        latency_ms=trace.latency_ms,
        prompt_tokens=trace.prompt_tokens,
        completion_tokens=trace.completion_tokens,
        total_tokens=trace.total_tokens,
        request_id=trace.request_id,
        entity_type=trace.entity_type,
        entity_id=str(trace.entity_id) if trace.entity_id else None,
        system_prompt=trace.system_prompt,
        user_prompt=trace.user_prompt,
        response_text=trace.response_text,
        thinking_text=trace.thinking_text,
        tool_calls=trace.tool_calls,
        extra_params=trace.extra_params,
        created_at=trace.created_at.isoformat(),
        updated_at=trace.updated_at.isoformat(),
        finished_at=trace.finished_at.isoformat() if trace.finished_at else None,
    )


async def list_llm_traces(
    db: AsyncSession,
    *,
    page: int = 1,
    size: int = 20,
    scene_key: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    query: str | None = None,
) -> LLMTraceListResponse:
    filtered = _apply_filters(
        select(LLMTrace),
        scene_key=scene_key,
        status=status,
        since=since,
        query=query,
    )

    stats_stmt = _apply_filters(
        select(
            func.count(LLMTrace.id),
            func.coalesce(func.sum(LLMTrace.total_tokens), 0),
            func.avg(LLMTrace.latency_ms),
            func.coalesce(func.sum(case((LLMTrace.status == "error", 1), else_=0)), 0),
        ),
        scene_key=scene_key,
        status=status,
        since=since,
        query=query,
    )
    stats_row = (await db.execute(stats_stmt)).one()
    total = int(stats_row[0] or 0)
    total_tokens = int(stats_row[1] or 0)
    avg_latency_ms = int(stats_row[2]) if stats_row[2] is not None else None
    error_count = int(stats_row[3] or 0)

    rows = (
        await db.execute(
            filtered.order_by(LLMTrace.created_at.desc())
            .offset((page - 1) * size)
            .limit(size + 1)
        )
    ).scalars().all()
    has_more = len(rows) > size
    traces = rows[:size]

    return LLMTraceListResponse(
        traces=[_to_list_item(trace) for trace in traces],
        stats=LLMTraceStatsResponse(
            total=total,
            total_tokens=total_tokens,
            avg_latency_ms=avg_latency_ms,
            error_rate=(error_count / total) if total else 0.0,
        ),
        has_more=has_more,
        page=page,
        total=total,
    )


async def get_llm_trace_detail(db: AsyncSession, trace_id) -> LLMTraceDetailResponse | None:
    trace = await db.get(LLMTrace, trace_id)
    if not trace:
        return None
    return _to_detail(trace)


async def clear_llm_traces(
    db: AsyncSession,
    *,
    before: datetime | None = None,
) -> LLMTraceClearResponse:
    stmt = select(func.count(LLMTrace.id))
    delete_stmt = delete(LLMTrace)
    if before:
        stmt = stmt.where(LLMTrace.created_at < before)
        delete_stmt = delete_stmt.where(LLMTrace.created_at < before)

    deleted_count = int((await db.execute(stmt)).scalar_one() or 0)
    await db.execute(delete_stmt)
    await db.commit()
    return LLMTraceClearResponse(deleted_count=deleted_count)


async def prune_llm_traces(
    db: AsyncSession,
    *,
    retention_days: int = 7,
    max_count: int = 5000,
) -> int:
    ids_to_delete = set()

    if retention_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        expired_ids = (
            await db.execute(
                select(LLMTrace.id).where(LLMTrace.created_at < cutoff)
            )
        ).scalars().all()
        ids_to_delete.update(expired_ids)

    if max_count > 0:
        overflow_ids = (
            await db.execute(
                select(LLMTrace.id)
                .order_by(LLMTrace.created_at.desc())
                .offset(max_count)
            )
        ).scalars().all()
        ids_to_delete.update(overflow_ids)

    if not ids_to_delete:
        return 0

    await db.execute(delete(LLMTrace).where(LLMTrace.id.in_(ids_to_delete)))
    await db.commit()
    return len(ids_to_delete)
