from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.media_provider_config import MediaProviderConfig


class MediaProviderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> MediaProviderConfig:
        config = MediaProviderConfig(**kwargs)
        self.session.add(config)
        await self.session.flush()
        return config

    async def get_by_id(self, config_id: uuid.UUID) -> MediaProviderConfig | None:
        return await self.session.get(MediaProviderConfig, config_id)

    async def list_all(self) -> list[MediaProviderConfig]:
        result = await self.session.execute(
            select(MediaProviderConfig).order_by(
                MediaProviderConfig.provider, MediaProviderConfig.created_at
            )
        )
        return list(result.scalars().all())

    async def list_by_provider(self, provider: str) -> list[MediaProviderConfig]:
        result = await self.session.execute(
            select(MediaProviderConfig).where(MediaProviderConfig.provider == provider)
        )
        return list(result.scalars().all())

    async def get_active_by_provider(self, provider: str) -> MediaProviderConfig | None:
        result = await self.session.execute(
            select(MediaProviderConfig).where(
                MediaProviderConfig.provider == provider,
                MediaProviderConfig.is_active == True,  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    async def list_active(self) -> list[MediaProviderConfig]:
        result = await self.session.execute(
            select(MediaProviderConfig).where(
                MediaProviderConfig.is_active == True  # noqa: E712
            )
        )
        return list(result.scalars().all())

    async def delete(self, config: MediaProviderConfig) -> None:
        await self.session.delete(config)
        await self.session.flush()
