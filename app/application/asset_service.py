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

_AUTO_TAG_SEMAPHORE: asyncio.Semaphore | None = None
AUTO_TAG_TIMEOUT_SECONDS = 120


def _auto_tag_semaphore() -> asyncio.Semaphore:
    """Limit concurrent vision tagging calls per app process.

    Local vision models such as Ollama/qwen3-vl can stall when several
    multi-megabyte image requests hit at once. Keep upload parsing moving by
    serializing the expensive AI tagging phase; metadata extraction and slice
    generation still run normally before this point.
    """
    global _AUTO_TAG_SEMAPHORE
    if _AUTO_TAG_SEMAPHORE is None:
        _AUTO_TAG_SEMAPHORE = asyncio.Semaphore(1)
    return _AUTO_TAG_SEMAPHORE


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
        content_form: list[str] | None = None,
        campaign_type: list[str] | None = None,
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
            content_form=content_form,
            campaign_type=campaign_type,
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

        # Hoisted so the except block can clean up an orphaned new
        # preview file when a later step (parser.parse, slice writes)
        # forces a rollback. Without this, the DB reverts to the old
        # preview_uri but the newly-written file sits on disk forever.
        new_preview_uri: str | None = None

        try:
            file_path = self.storage.get_absolute_path(asset.storage_uri)
            mime = asset.mime_type or ""

            # Step 1: extract metadata
            metadata = await extractor.extract(file_path, mime)
            await self.repo.update(asset, metadata_json=metadata)

            # Step 2: generate thumbnail.
            #
            # Convention shared by all visual asset types:
            #   - Fit within 512×512 box (shrink-only, never upscale)
            #   - JPEG quality 85 (sweet spot; <70 looks degraded, >90
            #     wastes bytes for marginal gain)
            #   - Stored under ``preview_uri``, served via
            #     /api/v1/assets/{id}/thumbnail
            #
            # Not extracted to a ThumbnailGenerator base class because we
            # have only two visual types (image, video) and they use
            # very different libraries (PIL vs ffmpeg). If a third type
            # ever genuinely needs a thumbnail (PDF first page, audio
            # waveform, 3D model render), revisit. Audio uses a generic
            # icon today, no thumbnail needed.
            #
            # Old-preview cleanup: when a re-parse runs (e.g. user
            # triggers a refresh, or the parse pipeline restarts after
            # a crash), the new thumbnail saves with a fresh UUID and
            # the old file is orphaned. We capture the previous URI
            # here but DEFER the actual deletion until after the final
            # ``session.commit()`` succeeds — otherwise a later step
            # (parser.parse, slice writes) could throw, the outer
            # except would rollback the DB to the old preview_uri,
            # and we'd be pointing at a file we already deleted.
            old_preview_uri = asset.preview_uri
            pending_old_preview_to_delete: str | None = None
            if mime.startswith("video/"):
                new_preview_uri = await self._generate_video_thumbnail(asset, file_path)
            elif mime.startswith("image/"):
                new_preview_uri = await self._generate_image_thumbnail(asset, file_path)
            if new_preview_uri:
                await self.repo.update(asset, preview_uri=new_preview_uri)
                if old_preview_uri and old_preview_uri != new_preview_uri:
                    pending_old_preview_to_delete = old_preview_uri

            # Step 3: generate slices
            slices_data = await parser.parse(str(asset.id), file_path, mime)
            for s in slices_data:
                await self.slice_repo.create(**s)

            # Step 4: AI auto-tagging. Keep the status as "processing"
            # while waiting for the per-process AI slot; only the asset
            # actively calling the vision model should show "tagging".
            try:
                async with _auto_tag_semaphore():
                    # Before invoking the model, clear any stale tag
                    # output so a re-parse of an already-tagged asset
                    # can't keep its old tags when this run silently
                    # fails (vision model misconfigured, network
                    # timeout, etc.). On success, ``_auto_tag``'s own
                    # update writes the fresh values back; on failure,
                    # the asset ends up with empty tags_json — which
                    # is what the "no tags" UI banner watches for.
                    await self.repo.update(
                        asset,
                        parse_status="tagging",
                        tags_json={},
                        confidence=None,
                        hook_score=None,
                        reuse_score=None,
                    )
                    await self.session.commit()
                    await asyncio.wait_for(
                        self._auto_tag(asset, metadata),
                        timeout=AUTO_TAG_TIMEOUT_SECONDS,
                    )
            except asyncio.TimeoutError:
                logger.warning(
                    "AI tagging timed out after %ss for asset %s, skipping",
                    AUTO_TAG_TIMEOUT_SECONDS,
                    asset_id,
                )
            except Exception:
                logger.warning("AI tagging failed for asset %s, skipping", asset_id, exc_info=True)

            # Mark done
            await self.repo.update(asset, parse_status="done")
            await self.session.commit()

            # Now that the new preview_uri is durably committed, it's
            # safe to delete the orphaned old file. If we deleted it
            # earlier, a parser.parse / slice-write failure would
            # rollback the DB to the OLD uri while the file was
            # already gone — a guaranteed broken-thumbnail state.
            # A failure here is non-fatal: the orphan wastes a few
            # KB until the next reprocess (or a janitor sweep).
            if pending_old_preview_to_delete:
                try:
                    await self.storage.delete_file(pending_old_preview_to_delete)
                except Exception as e:
                    logger.warning(
                        "Failed to delete old preview %s after parse for asset %s: %s",
                        pending_old_preview_to_delete, asset.id, e,
                    )

            logger.info("Parse completed for asset %s: %d slices", asset_id, len(slices_data))
            return asset

        except Exception as e:
            logger.error("Parse failed for asset %s: %s", asset_id, e)
            await self.session.rollback()
            # If we successfully wrote a new preview file but a later
            # step (parser.parse, slice insert) crashed, the rollback
            # above restored the DB's preview_uri to its prior value
            # (old uri or null) — the newly-written file is now
            # orphaned. Best-effort delete so retries don't accumulate
            # garbage thumbnails. Symmetric to the old-preview
            # deletion in the success path.
            if new_preview_uri:
                try:
                    await self.storage.delete_file(new_preview_uri)
                except Exception as cleanup_err:
                    logger.warning(
                        "Failed to clean up orphaned new preview %s "
                        "after parse error for asset %s: %s",
                        new_preview_uri, asset_id, cleanup_err,
                    )
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
            from app.infrastructure.knowledge_repo import KnowledgeItemRepository

            offer_repo = OfferRepository(self.session)
            offer = await offer_repo.get_by_id(asset.scope_id)
            if offer:
                language = offer.locale or language
                knowledge_repo = KnowledgeItemRepository(self.session)
                knowledge_items, _ = await knowledge_repo.list(
                    scope_type="offer",
                    scope_id=asset.scope_id,
                    knowledge_type=[
                        "selling_point",
                        "scenario",
                        "audience",
                        "pain_point",
                        "objection",
                        "proof",
                    ],
                    offset=0,
                    limit=30,
                )
                offer_context = {
                    "name": offer.name,
                    "positioning": offer.positioning,
                    "core_selling_points": offer.core_selling_points_json or [],
                    "target_scenarios": offer.target_scenarios_json or [],
                    "target_audience": offer.target_audience_json or [],
                    "knowledge_items": [
                        {
                            "knowledge_type": item.knowledge_type,
                            "title": item.title,
                            "content_raw": item.content_raw or "",
                        }
                        for item in knowledge_items
                    ],
                }

        # 2. Collect existing tags by category for consistency. Keep visual
        # subject tags out of the sample: they are facts about the current
        # image, and reusing old subject tags can hallucinate people/objects.
        existing_assets, _ = await self.repo.list(
            scope_type=asset.scope_type, scope_id=asset.scope_id, offset=0, limit=5
        )
        reusable_categories = (
            "usage", "selling_point", "scenario", "channel_fit",
            "content_form", "campaign_type",
        )
        existing_tags_by_category: dict[str, list[str]] = {cat: [] for cat in reusable_categories}
        for ea in existing_assets:
            if ea.tags_json and isinstance(ea.tags_json, dict):
                for cat in reusable_categories:
                    tags_list = ea.tags_json.get(cat)
                    if not isinstance(tags_list, list):
                        continue
                    for tag in tags_list:
                        if isinstance(tag, str) and tag not in existing_tags_by_category[cat]:
                            existing_tags_by_category[cat].append(tag)

        # 3. Build image path. For IMAGES, always feed the vision LLM
        # the original — packaging fine print, multi-subject photos,
        # poster captions, sale-tag text all degrade through the
        # 512×512 grid thumbnail and tag quality drops with them.
        # Pre-thumbnail-feature this happened naturally because images
        # had no preview_uri; once the thumbnail generator landed, the
        # AI pipeline silently switched to the smaller image. Forcing
        # storage_uri for image assets restores prior tagging quality.
        # Videos still use preview_uri because they have no other still
        # frame the model can analyze without expensive sampling.
        image_path = None
        if asset.asset_type == "image" and asset.storage_uri:
            image_path = self.storage.get_absolute_path(asset.storage_uri)
        elif asset.preview_uri:
            image_path = self.storage.get_absolute_path(asset.preview_uri)

        # 4. Call AI
        tag_input = {
            "file_name": asset.file_name,
            "asset_type": asset.asset_type,
            "mime_type": asset.mime_type,
            "existing_tags_sample": {
                cat: tags[:10]
                for cat, tags in existing_tags_by_category.items()
                if tags
            },
            **(metadata or {}),
        }
        result = await ai.extract_asset_tags(
            tag_input, image_path=image_path,
            offer_context=offer_context, language=language,
        )

        # 5. Store structured tags + scores
        # Wave 4: style/emotion dropped (0 consumers, generic noise);
        # content_form + campaign_type added as closed-vocabulary enums loaded
        # from app/apps/{content_forms,campaign_types}/*.md.
        tag_categories = ("subject", "usage", "selling_point", "scenario",
                          "channel_fit", "content_form", "campaign_type")
        tags_dict = {k: v for k, v in result.items()
                     if k in tag_categories and isinstance(v, list)}

        # Filter closed-vocabulary fields to valid ids only — AI occasionally
        # paraphrases or invents ids even when instructed not to.
        from app.application.campaign_types import list_campaign_types
        from app.application.content_forms import list_content_forms
        valid_content_form_ids = {cf.id for cf in list_content_forms()}
        valid_campaign_type_ids = {ct.id for ct in list_campaign_types()}
        if "content_form" in tags_dict:
            tags_dict["content_form"] = [
                x for x in tags_dict["content_form"] if x in valid_content_form_ids
            ]
        if "campaign_type" in tags_dict:
            tags_dict["campaign_type"] = [
                x for x in tags_dict["campaign_type"] if x in valid_campaign_type_ids
            ]

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
        """Use ffmpeg to extract frame at 1s and store as thumbnail.

        Scales the frame to fit within a 512×512 box without upscaling
        small inputs. Pre-fix this method emitted full-resolution
        frames, wasting bandwidth in the offer grid (~96-280px display)
        and the broll picker (96px). Convention matches the image
        thumbnail (PIL ``thumbnail((512, 512))``): both shrink-only,
        both bounded by a 512×512 box, both JPEG ~85.
        """
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
                # Fit within a 512×512 box, preserve aspect, never
                # upscale. The ``min(iw,512):min(ih,512)`` target makes
                # this shrink-only — without ``min`` ffmpeg's
                # ``decrease`` mode still scales up to the requested
                # dimensions when the input is smaller (verified: a
                # 200×200 input became 512×512). ``decrease`` then
                # adjusts the output to keep aspect within that target.
                "-vf", "scale='min(iw,512)':'min(ih,512)':force_original_aspect_ratio=decrease",
                "-q:v", "3",  # roughly JPEG quality ~85 in libjpeg's q-table
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

    async def _generate_image_thumbnail(self, asset: Asset, file_path: str) -> str | None:
        """Use Pillow to produce a short-edge 512px JPEG thumbnail.

        Image grids in offer.html, the B-roll picker, and the matched-
        asset card all need a small thumbnail; pre-fix they pulled the
        original file (sometimes 4MB+ for a 96px display). The original
        image is preserved on disk and remains the source for AI
        first_frame submission — only display paths shift to the
        compact thumbnail via ``preview_uri`` / ``/thumbnail`` endpoint.
        """
        import tempfile
        tmp_path = None
        try:
            from PIL import Image, ImageOps
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name

            with Image.open(file_path) as im:
                # ``ImageOps.exif_transpose`` honours camera orientation
                # so a portrait phone shot doesn't render rotated; many
                # PNG / WebP exports skip this and look wrong.
                im = ImageOps.exif_transpose(im)
                # ``thumbnail`` shrinks in-place preserving aspect, never
                # upscales — exactly what we want for a "short-edge cap".
                im.thumbnail((512, 512), Image.LANCZOS)
                # JPEG can't carry alpha; flatten transparent PNGs to
                # white. Animated GIFs only use the first frame.
                if im.mode in ("RGBA", "LA", "P"):
                    bg = Image.new("RGB", im.size, (255, 255, 255))
                    if im.mode == "P":
                        im = im.convert("RGBA")
                    bg.paste(im, mask=im.split()[-1] if im.mode in ("RGBA", "LA") else None)
                    im = bg
                elif im.mode != "RGB":
                    im = im.convert("RGB")
                im.save(tmp_path, "JPEG", quality=85, optimize=True)

            with open(tmp_path, "rb") as f:
                thumb_bytes = f.read()
            uri = await self.storage.save_file(
                thumb_bytes, f"{asset.id}_thumb.jpg", sub_path=str(asset.scope_id)
            )
            logger.info(
                "Image thumbnail generated for asset %s (%d bytes)",
                asset.id, len(thumb_bytes),
            )
            return uri

        except ImportError:
            logger.warning("Pillow not available, skipping image thumbnail for asset %s", asset.id)
        except Exception as e:
            logger.warning("Image thumbnail generation failed for asset %s: %s", asset.id, e)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        return None
