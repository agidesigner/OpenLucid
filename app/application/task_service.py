from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.task_run import TaskRun

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

TaskHandler = Callable[[AsyncSession, TaskRun], Awaitable[None]]
_HANDLERS: dict[str, TaskHandler] = {}


def register_task_handler(task_type: str) -> Callable[[TaskHandler], TaskHandler]:
    def _decorator(handler: TaskHandler) -> TaskHandler:
        _HANDLERS[task_type] = handler
        return handler

    return _decorator


async def enqueue_task(
    db: AsyncSession,
    *,
    task_type: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    params: dict[str, Any] | None = None,
    unique_key: str | None = None,
    max_attempts: int = 3,
) -> TaskRun:
    """Create a durable task, deduping active work by unique_key.

    The caller owns transaction commit. Returning an existing active row keeps
    repeated upload/retry clicks from spawning duplicate long-running work.
    """
    if unique_key:
        existing = (
            await db.execute(
                select(TaskRun).where(
                    TaskRun.unique_key == unique_key,
                    TaskRun.status.in_([STATUS_PENDING, STATUS_RUNNING]),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    task = TaskRun(
        task_type=task_type,
        entity_type=entity_type,
        entity_id=entity_id,
        params=params or {},
        unique_key=unique_key,
        max_attempts=max_attempts,
        status=STATUS_PENDING,
    )
    db.add(task)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        if not unique_key:
            raise
        existing = (
            await db.execute(
                select(TaskRun).where(
                    TaskRun.unique_key == unique_key,
                    TaskRun.status.in_([STATUS_PENDING, STATUS_RUNNING]),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        raise
    return task


async def enqueue_asset_parse(db: AsyncSession, asset_id: uuid.UUID) -> TaskRun:
    return await enqueue_task(
        db,
        task_type="asset.parse",
        entity_type="asset",
        entity_id=asset_id,
        unique_key=f"asset.parse:{asset_id}",
        max_attempts=2,
    )


async def enqueue_offer_model_inference(db: AsyncSession, offer_id: uuid.UUID) -> TaskRun:
    return await enqueue_task(
        db,
        task_type="offer.infer_model",
        entity_type="offer",
        entity_id=offer_id,
        unique_key=f"offer.infer_model:{offer_id}",
        max_attempts=2,
    )


async def requeue_stale_running_tasks(
    db: AsyncSession,
    *,
    max_age: timedelta = timedelta(minutes=10),
) -> int:
    """Return tasks left running by a dead worker back to the pending queue."""
    stale_before = datetime.now(timezone.utc) - max_age
    result = await db.execute(
        update(TaskRun)
        .where(
            TaskRun.status == STATUS_RUNNING,
            TaskRun.locked_at.isnot(None),
            TaskRun.locked_at < stale_before,
        )
        .values(
            status=STATUS_PENDING,
            locked_by=None,
            locked_at=None,
            error_message="Recovered from stale worker lock",
        )
    )
    return int(result.rowcount or 0)


async def task_worker_loop(
    *,
    worker_id: str | None = None,
    poll_interval: float = 2.0,
    batch_size: int = 1,
) -> None:
    worker_id = worker_id or f"{socket.gethostname()}:{uuid.uuid4()}"
    logger.info("Task worker started: %s", worker_id)
    while True:
        try:
            processed = await run_pending_tasks_once(worker_id=worker_id, limit=batch_size)
        except Exception:
            logger.warning("Task worker loop iteration failed", exc_info=True)
            processed = 0
        if processed == 0:
            await asyncio.sleep(poll_interval)


async def run_pending_tasks_once(*, worker_id: str, limit: int = 1) -> int:
    processed = 0
    for _ in range(limit):
        task_id = await _claim_next_task(worker_id)
        if task_id is None:
            break
        await _run_task(task_id)
        processed += 1
    return processed


async def _claim_next_task(worker_id: str) -> uuid.UUID | None:
    async with async_session_factory() as session:
        stmt = (
            select(TaskRun)
            .where(TaskRun.status == STATUS_PENDING)
            .order_by(TaskRun.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        task = (await session.execute(stmt)).scalar_one_or_none()
        if task is None:
            return None

        now = datetime.now(timezone.utc)
        task.status = STATUS_RUNNING
        task.locked_by = worker_id
        task.locked_at = now
        task.started_at = task.started_at or now
        task.attempts += 1
        task.error_message = None
        await session.commit()
        return task.id


async def _run_task(task_id: uuid.UUID) -> None:
    async with async_session_factory() as session:
        task = await session.get(TaskRun, task_id)
        if task is None:
            return

        handler = _HANDLERS.get(task.task_type)
        if handler is None:
            task.status = STATUS_FAILED
            task.error_message = f"No handler registered for task_type={task.task_type}"
            task.locked_by = None
            task.locked_at = None
            task.finished_at = datetime.now(timezone.utc)
            await session.commit()
            return

        try:
            await handler(session, task)
            task.status = STATUS_COMPLETED
            task.progress = 100
            task.error_message = None
            task.locked_by = None
            task.locked_at = None
            task.finished_at = datetime.now(timezone.utc)
            await session.commit()
        except Exception as exc:
            logger.warning("Task %s (%s) failed", task.id, task.task_type, exc_info=True)
            task.error_message = str(exc)[:4000]
            task.locked_by = None
            task.locked_at = None
            if task.attempts >= task.max_attempts:
                task.status = STATUS_FAILED
                task.finished_at = datetime.now(timezone.utc)
            else:
                task.status = STATUS_PENDING
            await session.commit()


@register_task_handler("asset.parse")
async def _handle_asset_parse(session: AsyncSession, task: TaskRun) -> None:
    if task.entity_id is None:
        raise ValueError("asset.parse requires entity_id")
    from app.adapters.asset_parser import LocalAssetParser, LocalMetadataExtractor
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService

    extractor = LocalMetadataExtractor()
    parser = LocalAssetParser(extractor)
    svc = AssetService(session, LocalStorageAdapter())
    await svc.run_parse(task.entity_id, extractor, parser)


@register_task_handler("offer.infer_model")
async def _handle_offer_model(session: AsyncSession, task: TaskRun) -> None:
    if task.entity_id is None:
        raise ValueError("offer.infer_model requires entity_id")
    from app.adapters.ai import get_ai_adapter
    from app.models.offer import Offer

    offer = await session.get(Offer, task.entity_id)
    if offer is None:
        logger.info("offer.infer_model skipped: offer %s not found", task.entity_id)
        return
    ai = await get_ai_adapter(session, scene_key="offer_model")
    model = await ai.infer_offer_model(
        name=offer.name,
        description=offer.description or "",
        offer_type=offer.offer_type,
    )
    offer.offer_model = model
    await session.flush()
