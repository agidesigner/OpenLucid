import asyncio
import io

import pytest
from httpx import AsyncClient
from PIL import Image


async def _create_merchant(client: AsyncClient) -> str:
    resp = await client.post("/api/v1/merchants", json={"name": "AM"})
    return resp.json()["id"]


def _make_png(width: int = 200, height: int = 150) -> bytes:
    """Generate a real PNG image in memory."""
    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --- Upload tests ---


@pytest.mark.asyncio
async def test_upload_asset(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    png_data = _make_png()
    resp = await client.post(
        "/api/v1/assets/upload",
        data={
            "scope_type": "merchant",
            "scope_id": merchant_id,
            "asset_type": "image",
        },
        files={"file": ("test.png", png_data, "image/png")},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["file_name"] == "test.png"
    assert data["mime_type"] == "image/png"
    assert data["asset_type"] == "image"
    assert data["scope_type"] == "merchant"
    assert data["parse_status"] == "pending"
    assert data["status"] == "raw"
    assert data["storage_uri"] is not None


@pytest.mark.asyncio
async def test_upload_video_asset(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    resp = await client.post(
        "/api/v1/assets/upload",
        data={
            "scope_type": "merchant",
            "scope_id": merchant_id,
            "asset_type": "video",
            "language": "en-US",
        },
        files={"file": ("demo.mp4", b"fake-video", "video/mp4")},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["asset_type"] == "video"
    assert data["language"] == "en-US"
    assert data["mime_type"] == "video/mp4"


# --- Get / List tests ---


@pytest.mark.asyncio
async def test_get_asset(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    png_data = _make_png()
    upload_resp = await client.post(
        "/api/v1/assets/upload",
        data={"scope_type": "merchant", "scope_id": merchant_id, "asset_type": "image"},
        files={"file": ("img.png", png_data, "image/png")},
    )
    asset_id = upload_resp.json()["id"]

    resp = await client.get(f"/api/v1/assets/{asset_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == asset_id


@pytest.mark.asyncio
async def test_get_asset_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/assets/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_assets_empty(client: AsyncClient):
    resp = await client.get("/api/v1/assets")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_assets_by_scope(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    png_data = _make_png()
    await client.post(
        "/api/v1/assets/upload",
        data={"scope_type": "merchant", "scope_id": merchant_id, "asset_type": "image"},
        files={"file": ("a.png", png_data, "image/png")},
    )
    await client.post(
        "/api/v1/assets/upload",
        data={"scope_type": "merchant", "scope_id": merchant_id, "asset_type": "image"},
        files={"file": ("b.png", png_data, "image/png")},
    )

    resp = await client.get(f"/api/v1/assets?scope_type=merchant&scope_id={merchant_id}")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


# --- Parse tests ---


@pytest.mark.asyncio
async def test_trigger_parse_image(client: AsyncClient):
    """Trigger parse on a real PNG and wait for background task to complete."""
    merchant_id = await _create_merchant(client)
    png_data = _make_png(320, 240)
    upload_resp = await client.post(
        "/api/v1/assets/upload",
        data={"scope_type": "merchant", "scope_id": merchant_id, "asset_type": "image"},
        files={"file": ("photo.png", png_data, "image/png")},
    )
    asset_id = upload_resp.json()["id"]

    # Trigger parse
    parse_resp = await client.post(f"/api/v1/assets/{asset_id}/parse")
    assert parse_resp.status_code == 200

    # Wait for background task
    await asyncio.sleep(0.5)

    # Check asset metadata was extracted
    get_resp = await client.get(f"/api/v1/assets/{asset_id}")
    data = get_resp.json()
    assert data["parse_status"] == "done"
    assert data["metadata_json"]["width"] == 320
    assert data["metadata_json"]["height"] == 240
    assert data["metadata_json"]["format"] == "PNG"

    # Check slice was created
    slices_resp = await client.get(f"/api/v1/assets/{asset_id}/slices")
    slices = slices_resp.json()
    assert len(slices) == 1
    assert slices[0]["slice_type"] == "frame"


@pytest.mark.asyncio
async def test_trigger_parse_not_found(client: AsyncClient):
    resp = await client.post("/api/v1/assets/00000000-0000-0000-0000-000000000000/parse")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_slices_empty(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    png_data = _make_png()
    upload_resp = await client.post(
        "/api/v1/assets/upload",
        data={"scope_type": "merchant", "scope_id": merchant_id, "asset_type": "image"},
        files={"file": ("s.png", png_data, "image/png")},
    )
    asset_id = upload_resp.json()["id"]

    resp = await client.get(f"/api/v1/assets/{asset_id}/slices")
    assert resp.status_code == 200
    assert resp.json() == []
