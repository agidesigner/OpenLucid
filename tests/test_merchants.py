import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_merchant(client: AsyncClient):
    resp = await client.post("/api/v1/merchants", json={"name": "Test Shop", "merchant_type": "goods"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Shop"
    assert data["merchant_type"] == "goods"
    assert data["default_locale"] == "zh-CN"
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_create_merchant_service_type(client: AsyncClient):
    resp = await client.post("/api/v1/merchants", json={
        "name": "Consulting Co",
        "merchant_type": "service",
        "default_locale": "en-US",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["merchant_type"] == "service"
    assert data["default_locale"] == "en-US"


@pytest.mark.asyncio
async def test_create_merchant_with_profiles(client: AsyncClient):
    resp = await client.post("/api/v1/merchants", json={
        "name": "Brand Shop",
        "merchant_type": "goods",
        "brand_profile_json": {"slogan": "Best in class", "colors": ["#FF0000"]},
        "tone_profile_json": {"style": "professional", "voice": "authoritative"},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["brand_profile_json"]["slogan"] == "Best in class"
    assert data["tone_profile_json"]["style"] == "professional"


@pytest.mark.asyncio
async def test_create_merchant_validation_empty_name(client: AsyncClient):
    resp = await client.post("/api/v1/merchants", json={"name": ""})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_merchant(client: AsyncClient):
    create_resp = await client.post("/api/v1/merchants", json={"name": "Shop A"})
    merchant_id = create_resp.json()["id"]

    resp = await client.get(f"/api/v1/merchants/{merchant_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Shop A"


@pytest.mark.asyncio
async def test_get_merchant_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/merchants/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_list_merchants(client: AsyncClient):
    await client.post("/api/v1/merchants", json={"name": "Shop 1"})
    await client.post("/api/v1/merchants", json={"name": "Shop 2"})
    await client.post("/api/v1/merchants", json={"name": "Shop 3"})

    resp = await client.get("/api/v1/merchants")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 3
    assert len(data["items"]) >= 3
    assert data["page"] == 1
    assert data["page_size"] == 20


@pytest.mark.asyncio
async def test_list_merchants_pagination(client: AsyncClient):
    for i in range(5):
        await client.post("/api/v1/merchants", json={"name": f"Shop {i}"})

    resp = await client.get("/api/v1/merchants?page=1&page_size=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["total"] >= 5
    assert data["page_size"] == 2


@pytest.mark.asyncio
async def test_update_merchant(client: AsyncClient):
    create_resp = await client.post("/api/v1/merchants", json={"name": "Old Name"})
    merchant_id = create_resp.json()["id"]

    resp = await client.patch(f"/api/v1/merchants/{merchant_id}", json={"name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_update_merchant_partial(client: AsyncClient):
    create_resp = await client.post("/api/v1/merchants", json={
        "name": "Original",
        "merchant_type": "goods",
    })
    merchant_id = create_resp.json()["id"]

    resp = await client.patch(f"/api/v1/merchants/{merchant_id}", json={"merchant_type": "hybrid"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Original"
    assert data["merchant_type"] == "hybrid"


@pytest.mark.asyncio
async def test_update_merchant_not_found(client: AsyncClient):
    resp = await client.patch(
        "/api/v1/merchants/00000000-0000-0000-0000-000000000000",
        json={"name": "Ghost"},
    )
    assert resp.status_code == 404
