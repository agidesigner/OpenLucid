import io
import os
import tempfile

import pytest
from PIL import Image

from app.adapters.asset_parser import LocalAssetParser, LocalMetadataExtractor, StubMetadataExtractor


@pytest.mark.asyncio
async def test_stub_extractor():
    ext = StubMetadataExtractor()
    result = await ext.extract("/fake/path", "image/png")
    assert result["extractor"] == "stub"


@pytest.mark.asyncio
async def test_image_metadata_extraction():
    ext = LocalMetadataExtractor()

    # Create a real temp PNG
    img = Image.new("RGB", (640, 480), color=(0, 128, 255))
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img.save(f, format="PNG")
        tmp_path = f.name

    try:
        result = await ext.extract(tmp_path, "image/png")
        assert result["width"] == 640
        assert result["height"] == 480
        assert result["format"] == "PNG"
        assert result["file_size"] > 0
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_image_parser_generates_frame_slice():
    ext = LocalMetadataExtractor()
    parser = LocalAssetParser(ext)

    img = Image.new("RGB", (100, 100))
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        img.save(f, format="JPEG")
        tmp_path = f.name

    try:
        slices = await parser.parse("test-asset-id", tmp_path, "image/jpeg")
        assert len(slices) == 1
        assert slices[0]["slice_type"] == "frame"
        assert slices[0]["asset_id"] == "test-asset-id"
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_nonexistent_file_extraction():
    ext = LocalMetadataExtractor()
    result = await ext.extract("/nonexistent/file.png", "image/png")
    assert result["file_size"] == 0
    assert "error" in result
