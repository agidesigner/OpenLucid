from __future__ import annotations

import uuid

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_processing_job import AssetProcessingJob
from app.models.asset_slice import AssetSlice


class AssetRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> Asset:
        asset = Asset(**kwargs)
        self.session.add(asset)
        await self.session.flush()
        return asset

    async def get_by_id(self, asset_id: uuid.UUID) -> Asset | None:
        return await self.session.get(Asset, asset_id)

    async def find_by_hash(
        self,
        file_hash: str,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
    ) -> Asset | None:
        stmt = select(Asset).where(Asset.file_hash == file_hash)
        if scope_type:
            stmt = stmt.where(Asset.scope_type == scope_type)
        if scope_id:
            stmt = stmt.where(Asset.scope_id == scope_id)
        result = await self.session.execute(stmt.limit(1))
        return result.scalars().first()

    async def list(
        self,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Asset], int]:
        base = select(Asset)
        count_base = select(func.count()).select_from(Asset)

        if scope_type:
            base = base.where(Asset.scope_type == scope_type)
            count_base = count_base.where(Asset.scope_type == scope_type)
        if scope_id:
            base = base.where(Asset.scope_id == scope_id)
            count_base = count_base.where(Asset.scope_id == scope_id)

        total = (await self.session.execute(count_base)).scalar_one()
        stmt = base.offset(offset).limit(limit).order_by(Asset.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def search(
        self,
        q: str | None = None,
        asset_type: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
        content_form: list[str] | None = None,
        campaign_type: list[str] | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Asset], int]:
        base = select(Asset)
        count_base = select(func.count()).select_from(Asset)

        if scope_type:
            base = base.where(Asset.scope_type == scope_type)
            count_base = count_base.where(Asset.scope_type == scope_type)
        if scope_id:
            base = base.where(Asset.scope_id == scope_id)
            count_base = count_base.where(Asset.scope_id == scope_id)
        if asset_type:
            base = base.where(Asset.asset_type == asset_type)
            count_base = count_base.where(Asset.asset_type == asset_type)
        if status:
            base = base.where(Asset.status == status)
            count_base = count_base.where(Asset.status == status)
        if q:
            pattern = f"%{q}%"
            tag_match = text(
                "EXISTS (SELECT 1 FROM jsonb_each(assets.tags_json) kv,"
                " jsonb_array_elements_text(kv.value) t"
                " WHERE t.value ILIKE :q_pattern)"
            ).bindparams(q_pattern=pattern)
            keyword_filter = (Asset.title.ilike(pattern)) | (Asset.file_name.ilike(pattern)) | (tag_match)
            base = base.where(keyword_filter)
            count_base = count_base.where(keyword_filter)
        if tags:
            for i, tag in enumerate(tags):
                param_name = f"tag_{i}"
                tag_exists = text(
                    f"EXISTS (SELECT 1 FROM jsonb_each(assets.tags_json) kv,"
                    f" jsonb_array_elements_text(kv.value) t"
                    f" WHERE t.value = :{param_name})"
                ).bindparams(**{param_name: tag})
                base = base.where(tag_exists)
                count_base = count_base.where(tag_exists)

        # Wave 5 — closed-vocab filters: check a specific category key contains
        # any of the given ids. Uses JSONB ?| (any-of) on the category's array.
        # Example: content_form=["unboxing","review"] matches assets whose
        # tags_json.content_form includes at least one of those.
        for category_key, values in [
            ("content_form", content_form),
            ("campaign_type", campaign_type),
        ]:
            if values:
                cat_filter = text(
                    f"(assets.tags_json -> :{category_key}_key) ?| :{category_key}_ids"
                ).bindparams(**{
                    f"{category_key}_key": category_key,
                    f"{category_key}_ids": list(values),
                })
                base = base.where(cat_filter)
                count_base = count_base.where(cat_filter)

        total = (await self.session.execute(count_base)).scalar_one()
        stmt = base.offset(offset).limit(limit).order_by(Asset.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_highlights(
        self,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
        min_hook_score: float = 0.0,
        min_proof_score: float = 0.0,
        min_reuse_score: float = 0.0,
        slice_type: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[AssetSlice], int]:
        base = select(AssetSlice).join(Asset, AssetSlice.asset_id == Asset.id)
        count_base = select(func.count()).select_from(AssetSlice).join(Asset, AssetSlice.asset_id == Asset.id)

        if scope_type:
            base = base.where(Asset.scope_type == scope_type)
            count_base = count_base.where(Asset.scope_type == scope_type)
        if scope_id:
            base = base.where(Asset.scope_id == scope_id)
            count_base = count_base.where(Asset.scope_id == scope_id)
        if slice_type:
            base = base.where(AssetSlice.slice_type == slice_type)
            count_base = count_base.where(AssetSlice.slice_type == slice_type)
        if min_hook_score > 0:
            base = base.where(AssetSlice.hook_score >= min_hook_score)
            count_base = count_base.where(AssetSlice.hook_score >= min_hook_score)
        if min_proof_score > 0:
            base = base.where(AssetSlice.proof_score >= min_proof_score)
            count_base = count_base.where(AssetSlice.proof_score >= min_proof_score)
        if min_reuse_score > 0:
            base = base.where(AssetSlice.reuse_score >= min_reuse_score)
            count_base = count_base.where(AssetSlice.reuse_score >= min_reuse_score)

        total = (await self.session.execute(count_base)).scalar_one()
        stmt = base.offset(offset).limit(limit).order_by(AssetSlice.hook_score.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_tag_analytics(
        self,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
        asset_type: str | None = None,
        category: str | None = None,
    ) -> list[dict]:
        where_clauses = ["a.tags_json IS NOT NULL"]
        params: dict = {}
        if scope_type:
            where_clauses.append("a.scope_type = :scope_type")
            params["scope_type"] = scope_type
        if scope_id:
            where_clauses.append("a.scope_id = :scope_id")
            params["scope_id"] = str(scope_id)
        if asset_type:
            where_clauses.append("a.asset_type = :asset_type")
            params["asset_type"] = asset_type
        if category:
            where_clauses.append("kv.key = :category")
            params["category"] = category

        where_sql = " AND ".join(where_clauses)
        # Support both structured dict (new) and legacy flat array formats
        sql = text(f"""
            SELECT kv.key AS category, tag.value AS tag, COUNT(*) AS cnt
            FROM assets a,
                 jsonb_each(a.tags_json) AS kv(key, value),
                 jsonb_array_elements_text(kv.value) AS tag(value)
            WHERE jsonb_typeof(a.tags_json) = 'object'
              AND {where_sql}
            GROUP BY kv.key, tag.value
            ORDER BY cnt DESC
        """)
        result = await self.session.execute(sql, params)
        return [{"tag": row.tag, "count": row.cnt, "category": row.category} for row in result]

    async def update(self, asset: Asset, **kwargs) -> Asset:
        for key, value in kwargs.items():
            if value is not None:
                setattr(asset, key, value)
        await self.session.flush()
        await self.session.refresh(asset)
        return asset


class AssetSliceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> AssetSlice:
        slice_obj = AssetSlice(**kwargs)
        self.session.add(slice_obj)
        await self.session.flush()
        return slice_obj

    async def list_by_asset(self, asset_id: uuid.UUID) -> list[AssetSlice]:
        stmt = select(AssetSlice).where(AssetSlice.asset_id == asset_id).order_by(AssetSlice.start_ms)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class AssetProcessingJobRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> AssetProcessingJob:
        job = AssetProcessingJob(**kwargs)
        self.session.add(job)
        await self.session.flush()
        return job

    async def list_by_asset(self, asset_id: uuid.UUID) -> list[AssetProcessingJob]:
        stmt = (
            select(AssetProcessingJob)
            .where(AssetProcessingJob.asset_id == asset_id)
            .order_by(AssetProcessingJob.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
