from __future__ import annotations

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class MetadataExtractor(ABC):
    @abstractmethod
    async def extract(self, file_path: str, mime_type: str) -> dict[str, Any]:
        """Extract metadata from a file. Returns dict with keys like width, height, duration_ms, etc."""


class AssetParser(ABC):
    @abstractmethod
    async def parse(self, asset_id: str, file_path: str, mime_type: str) -> list[dict[str, Any]]:
        """Parse asset and return list of slice dicts."""


class StubMetadataExtractor(MetadataExtractor):
    async def extract(self, file_path: str, mime_type: str) -> dict[str, Any]:
        return {
            "file_path": file_path,
            "mime_type": mime_type,
            "extractor": "stub",
        }


class StubAssetParser(AssetParser):
    async def parse(self, asset_id: str, file_path: str, mime_type: str) -> list[dict[str, Any]]:
        return []


class LocalMetadataExtractor(MetadataExtractor):
    """Extracts metadata using Pillow (images) and ffprobe (video/audio)."""

    async def extract(self, file_path: str, mime_type: str) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "file_size": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
            "mime_type": mime_type,
        }

        if mime_type.startswith("image/"):
            meta.update(await self._extract_image(file_path))
        elif mime_type.startswith("video/"):
            meta.update(await self._extract_video(file_path))
        elif mime_type.startswith("audio/"):
            meta.update(await self._extract_audio(file_path))

        return meta

    async def _extract_image(self, file_path: str) -> dict[str, Any]:
        try:
            from PIL import Image
            img = await asyncio.to_thread(Image.open, file_path)
            result = {
                "width": img.width,
                "height": img.height,
                "format": img.format,
                "mode": img.mode,
            }
            img.close()
            return result
        except Exception as e:
            logger.warning("Image metadata extraction failed for %s: %s", file_path, e)
            return {"error": str(e)}

    async def _extract_video(self, file_path: str) -> dict[str, Any]:
        return await self._run_ffprobe(file_path)

    async def _extract_audio(self, file_path: str) -> dict[str, Any]:
        return await self._run_ffprobe(file_path)

    async def _run_ffprobe(self, file_path: str) -> dict[str, Any]:
        try:
            cmd = [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                file_path,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return {"error": "ffprobe failed"}

            probe = json.loads(stdout)
            result: dict[str, Any] = {}

            fmt = probe.get("format", {})
            if "duration" in fmt:
                result["duration_ms"] = int(float(fmt["duration"]) * 1000)
            if "bit_rate" in fmt:
                result["bit_rate"] = int(fmt["bit_rate"])

            for stream in probe.get("streams", []):
                if stream.get("codec_type") == "video":
                    result["width"] = stream.get("width")
                    result["height"] = stream.get("height")
                    result["codec"] = stream.get("codec_name")
                    # frame rate
                    fps_str = stream.get("r_frame_rate", "0/1")
                    parts = fps_str.split("/")
                    if len(parts) == 2 and int(parts[1]) > 0:
                        result["fps"] = round(int(parts[0]) / int(parts[1]), 2)
                    break
                elif stream.get("codec_type") == "audio" and "codec" not in result:
                    result["codec"] = stream.get("codec_name")
                    result["sample_rate"] = stream.get("sample_rate")
                    result["channels"] = stream.get("channels")

            return result
        except FileNotFoundError:
            logger.warning("ffprobe not found, skipping probe for %s", file_path)
            return {"error": "ffprobe not available"}
        except Exception as e:
            logger.warning("ffprobe failed for %s: %s", file_path, e)
            return {"error": str(e)}


class LocalAssetParser(AssetParser):
    """Generates basic slices for video assets based on duration."""

    def __init__(self, extractor: MetadataExtractor):
        self.extractor = extractor

    async def parse(self, asset_id: str, file_path: str, mime_type: str) -> list[dict[str, Any]]:
        meta = await self.extractor.extract(file_path, mime_type)
        slices: list[dict[str, Any]] = []

        if mime_type.startswith("video/") and "duration_ms" in meta:
            slices = self._generate_video_slices(asset_id, meta)
        elif mime_type.startswith("image/"):
            slices = [self._generate_image_slice(asset_id, meta)]

        return slices

    def _generate_video_slices(self, asset_id: str, meta: dict[str, Any]) -> list[dict[str, Any]]:
        duration_ms = meta["duration_ms"]
        segment_ms = 30_000  # 30-second segments
        slices = []
        start = 0
        idx = 0
        while start < duration_ms:
            end = min(start + segment_ms, duration_ms)
            slices.append({
                "asset_id": asset_id,
                "slice_type": "clip",
                "start_ms": start,
                "end_ms": end,
                "summary": f"Segment {idx + 1} ({start // 1000}s - {end // 1000}s)",
            })
            start = end
            idx += 1
        return slices

    def _generate_image_slice(self, asset_id: str, meta: dict[str, Any]) -> dict[str, Any]:
        return {
            "asset_id": asset_id,
            "slice_type": "frame",
            "start_ms": 0,
            "end_ms": 0,
            "summary": f"Image {meta.get('width', '?')}x{meta.get('height', '?')}",
        }
