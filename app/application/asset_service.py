from __future__ import annotations

import asyncio
import logging
import os
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.asset_parser import AssetParser, MetadataExtractor
from app.adapters.storage import StorageAdapter
from app.exceptions import NotFoundError
from app.infrastructure.asset_repo import AssetProcessingJobRepository, AssetRepository, AssetSliceRepository
from app.models.asset import Asset
from app.models.asset_slice import AssetSlice
from app.schemas.asset import AssetCopyCreate, AssetUploadMeta

logger = logging.getLogger(__name__)


class AssetService:
    def __init__(self, session: AsyncSession, storage: StorageAdapter):
        self.session = session
        self.repo = AssetRepository(session)
        self.slice_repo = AssetSliceRepository(session)
        self.job_repo = AssetProcessingJobRepository(session)
        self.storage = storage

    async def upload(
        self,
        file_content: bytes,
        file_name: str,
        mime_type: str | None,
        meta: AssetUploadMeta,
    ) -> Asset:
        import hashlib
        file_hash = hashlib.sha256(file_content).hexdigest()
        storage_uri = await self.storage.save_file(
            file_content, file_name, sub_path=str(meta.scope_id)
        )
        return await self.repo.create(
            scope_type=meta.scope_type.value,
            scope_id=meta.scope_id,
            asset_type=meta.asset_type.value,
            file_name=file_name,
            mime_type=mime_type,
            storage_uri=storage_uri,
            language=meta.language,
            file_hash=file_hash,
        )

    async def check_duplicate(
        self,
        file_hash: str,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
    ) -> Asset | None:
        return await self.repo.find_by_hash(file_hash, scope_type=scope_type, scope_id=scope_id)

    async def create_copy(self, data: AssetCopyCreate) -> Asset:
        """Create a copy (text) asset — no file, parse_status immediately done."""
        tags_json = data.tags if data.tags else None
        return await self.repo.create(
            scope_type=data.scope_type.value,
            scope_id=data.scope_id,
            asset_type="copy",
            file_name=data.title,
            title=data.title,
            content_text=data.content_text,
            tags_json=tags_json,
            language=data.language,
            parse_status="done",
        )

    async def get(self, asset_id: uuid.UUID) -> Asset:
        asset = await self.repo.get_by_id(asset_id)
        if not asset:
            raise NotFoundError("Asset", str(asset_id))
        return asset

    async def list(
        self,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Asset], int]:
        offset = (page - 1) * page_size
        return await self.repo.list(
            scope_type=scope_type, scope_id=scope_id, offset=offset, limit=page_size
        )

    async def search(
        self,
        q: str | None = None,
        asset_type: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Asset], int]:
        offset = (page - 1) * page_size
        return await self.repo.search(
            q=q,
            asset_type=asset_type,
            tags=tags,
            status=status,
            scope_type=scope_type,
            scope_id=scope_id,
            offset=offset,
            limit=page_size,
        )

    async def get_highlights(
        self,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
        min_hook_score: float = 0.0,
        min_proof_score: float = 0.0,
        min_reuse_score: float = 0.0,
        slice_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AssetSlice], int]:
        offset = (page - 1) * page_size
        return await self.repo.get_highlights(
            scope_type=scope_type,
            scope_id=scope_id,
            min_hook_score=min_hook_score,
            min_proof_score=min_proof_score,
            min_reuse_score=min_reuse_score,
            slice_type=slice_type,
            offset=offset,
            limit=page_size,
        )

    async def get_tag_analytics(
        self,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
        asset_type: str | None = None,
        category: str | None = None,
    ) -> list[dict]:
        return await self.repo.get_tag_analytics(
            scope_type=scope_type,
            scope_id=scope_id,
            asset_type=asset_type,
            category=category,
        )

    async def get_processing_jobs(self, asset_id: uuid.UUID):
        await self.get(asset_id)
        return await self.job_repo.list_by_asset(asset_id)

    async def get_slices(self, asset_id: uuid.UUID) -> list[AssetSlice]:
        await self.get(asset_id)
        return await self.slice_repo.list_by_asset(asset_id)

    async def update_asset(
        self,
        asset_id: uuid.UUID,
        title: str | None = None,
        tags_json: dict | list | None = None,
    ) -> Asset:
        asset = await self.get(asset_id)
        kwargs: dict = {}
        if title is not None:
            kwargs["title"] = title
        if tags_json is not None:
            kwargs["tags_json"] = tags_json
        if kwargs:
            await self.repo.update(asset, **kwargs)
        await self.session.commit()
        await self.session.refresh(asset)
        return asset

    async def delete_asset(self, asset_id: uuid.UUID) -> None:
        asset = await self.get(asset_id)
        # Delete stored files
        for uri in [asset.storage_uri, asset.preview_uri]:
            if uri:
                try:
                    await self.storage.delete_file(uri)
                except Exception as e:
                    logger.warning("Failed to delete file %s: %s", uri, e)
        await self.session.delete(asset)
        await self.session.flush()
        await self.session.commit()

    async def run_parse(
        self,
        asset_id: uuid.UUID,
        extractor: MetadataExtractor,
        parser: AssetParser,
    ) -> Asset:
        """Execute metadata extraction + slice generation for an asset."""
        asset = await self.get(asset_id)

        # Mark as processing
        await self.repo.update(asset, parse_status="processing")
        await self.session.commit()

        try:
            file_path = self.storage.get_absolute_path(asset.storage_uri)
            mime = asset.mime_type or ""

            # Step 1: extract metadata
            metadata = await extractor.extract(file_path, mime)
            await self.repo.update(asset, metadata_json=metadata)

            # Step 2: generate thumbnail for video
            if mime.startswith("video/"):
                preview_uri = await self._generate_video_thumbnail(asset, file_path)
                if preview_uri:
                    await self.repo.update(asset, preview_uri=preview_uri)

            # Step 3: generate slices
            slices_data = await parser.parse(str(asset.id), file_path, mime)
            for s in slices_data:
                await self.slice_repo.create(**s)

            # Step 4: AI auto-tagging
            await self.repo.update(asset, parse_status="tagging")
            await self.session.commit()
            try:
                await self._auto_tag(asset, metadata)
            except Exception:
                logger.warning("AI tagging failed for asset %s, skipping", asset_id, exc_info=True)

            # Mark done
            await self.repo.update(asset, parse_status="done")
            await self.session.commit()

            logger.info("Parse completed for asset %s: %d slices", asset_id, len(slices_data))
            return asset

        except Exception as e:
            logger.error("Parse failed for asset %s: %s", asset_id, e)
            await self.session.rollback()
            # Re-fetch after rollback
            asset = await self.repo.get_by_id(asset_id)
            if asset:
                await self.repo.update(asset, parse_status="failed")
                await self.session.commit()
            raise

    async def _auto_tag(self, asset: Asset, metadata: dict) -> None:
        """Use AI (vision LLM preferred) to generate structured tags from asset."""
        from app.adapters.ai import get_ai_adapter, StubAIAdapter

        ai = await get_ai_adapter(self.session, scene_key="asset_tagging", model_type="vision_llm")
        if isinstance(ai, StubAIAdapter):
            logger.info("No AI configured for asset_tagging scene, skipping auto-tag for asset %s", asset.id)
            return

        # 1. Load Offer context
        offer_context = None
        language = asset.language or "zh-CN"
        if asset.scope_type == "offer":
            from app.infrastructure.offer_repo import OfferRepository
            offer_repo = OfferRepository(self.session)
            offer = await offer_repo.get_by_id(asset.scope_id)
            if offer:
                language = offer.locale or language
                offer_context = {
                    "name": offer.name,
                    "positioning": offer.positioning,
                    "core_selling_points": offer.core_selling_points_json or [],
                    "target_scenarios": offer.target_scenarios_json or [],
                    "target_audience": offer.target_audience_json or [],
                }

        # 2. Collect existing tags sample for consistency
        existing_assets, _ = await self.repo.list(
            scope_type=asset.scope_type, scope_id=asset.scope_id, offset=0, limit=5
        )
        existing_tags: set[str] = set()
        for ea in existing_assets:
            if ea.tags_json and isinstance(ea.tags_json, dict):
                for tags_list in ea.tags_json.values():
                    if isinstance(tags_list, list):
                        existing_tags.update(tags_list)

        # 3. Build image path
        image_path = None
        if asset.preview_uri:
            image_path = self.storage.get_absolute_path(asset.preview_uri)
        elif asset.asset_type == "image" and asset.storage_uri:
            image_path = self.storage.get_absolute_path(asset.storage_uri)

        # 4. Call AI
        tag_input = {
            "file_name": asset.file_name,
            "asset_type": asset.asset_type,
            "mime_type": asset.mime_type,
            "existing_tags_sample": list(existing_tags)[:30],
            **(metadata or {}),
        }
        result = await ai.extract_asset_tags(
            tag_input, image_path=image_path,
            offer_context=offer_context, language=language,
        )

        # 5. Store structured tags + scores
        tag_categories = ("subject", "usage", "selling_point", "scenario",
                          "channel_fit", "style", "emotion")
        tags_dict = {k: v for k, v in result.items()
                     if k in tag_categories and isinstance(v, list)}
        confidence = result.get("confidence", 0.0)
        hook_score = result.get("hook_score")
        reuse_score = result.get("reuse_score")

        update_kwargs: dict = {"tags_json": tags_dict, "confidence": confidence}
        if hook_score is not None:
            update_kwargs["hook_score"] = float(hook_score)
        if reuse_score is not None:
            update_kwargs["reuse_score"] = float(reuse_score)

        await self.repo.update(asset, **update_kwargs)
        await self.session.commit()
        tag_count = sum(len(v) for v in tags_dict.values())
        logger.info("Auto-tagged asset %s with %d structured tags (confidence=%.2f)", asset.id, tag_count, confidence)

    async def _generate_video_thumbnail(self, asset: Asset, file_path: str) -> str | None:
        """Use ffmpeg to extract frame at 1s and store as thumbnail."""
        import tempfile
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name

            cmd = [
                "ffmpeg", "-y",
                "-i", file_path,
                "-ss", "00:00:01",
                "-frames:v", "1",
                "-q:v", "2",
                tmp_path,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()

            if proc.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                with open(tmp_path, "rb") as f:
                    thumb_bytes = f.read()
                uri = await self.storage.save_file(
                    thumb_bytes, f"{asset.id}_thumb.jpg", sub_path=str(asset.scope_id)
                )
                logger.info("Thumbnail generated for asset %s", asset.id)
                return uri

        except FileNotFoundError:
            logger.warning("ffmpeg not found, skipping thumbnail for asset %s", asset.id)
        except Exception as e:
            logger.warning("Thumbnail generation failed for asset %s: %s", asset.id, e)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        return None
