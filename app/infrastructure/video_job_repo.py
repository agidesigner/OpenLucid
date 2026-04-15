from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.video_generation_job import VideoGenerationJob


class VideoJobRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> VideoGenerationJob:
        job = VideoGenerationJob(**kwargs)
        self.session.add(job)
        await self.session.flush()
        return job

    async def get_by_id(self, job_id: uuid.UUID) -> VideoGenerationJob | None:
        return await self.session.get(VideoGenerationJob, job_id)

    async def list_for_creation(
        self, creation_id: uuid.UUID
    ) -> list[VideoGenerationJob]:
        result = await self.session.execute(
            select(VideoGenerationJob)
            .where(VideoGenerationJob.creation_id == creation_id)
            .order_by(VideoGenerationJob.created_at.desc())
        )
        return list(result.scalars().all())

    async def delete(self, job: VideoGenerationJob) -> None:
        await self.session.delete(job)
        await self.session.flush()
