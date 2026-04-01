import pytest
from httpx import AsyncClient


async def _create_merchant(client: AsyncClient, name: str = "Test Merchant") -> str:
    resp = await client.post("/api/v1/merchants", json={"name": name})
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_offer(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    resp = await client.post("/api/v1/offers", json={
        "merchant_id": merchant_id,
        "name": "Test Product",
        "offer_type": "product",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Product"
    assert data["merchant_id"] == merchant_id
    assert data["offer_type"] == "product"
    assert data["locale"] == "zh-CN"
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_create_offer_service_type(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    resp = await client.post("/api/v1/offers", json={
        "merchant_id": merchant_id,
        "name": "Consulting Package",
        "offer_type": "service",
        "offer_model": "consulting_service",
        "description": "Full stack consulting",
        "positioning": "Premium B2B",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["offer_type"] == "service"
    assert data["offer_model"] == "consulting_service"
    assert data["description"] == "Full stack consulting"


@pytest.mark.asyncio
async def test_create_offer_with_json_fields(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    resp = await client.post("/api/v1/offers", json={
        "merchant_id": merchant_id,
        "name": "Smart Widget",
        "target_audience_json": {"age": "25-35", "gender": "all"},
        "core_selling_points_json": {"points": ["fast", "reliable", "affordable"]},
        "pricing_info_json": {"price": 99.9, "currency": "CNY"},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["target_audience_json"]["age"] == "25-35"
    assert "fast" in data["core_selling_points_json"]["points"]
    assert data["pricing_info_json"]["price"] == 99.9


@pytest.mark.asyncio
async def test_create_offer_invalid_merchant(client: AsyncClient):
    resp = await client.post("/api/v1/offers", json={
        "merchant_id": "00000000-0000-0000-0000-000000000000",
        "name": "Orphan Product",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_offer(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    create_resp = await client.post("/api/v1/offers", json={
        "merchant_id": merchant_id, "name": "Widget"
    })
    offer_id = create_resp.json()["id"]

    resp = await client.get(f"/api/v1/offers/{offer_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Widget"


@pytest.mark.asyncio
async def test_get_offer_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/offers/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_offers_by_merchant(client: AsyncClient):
    m1 = await _create_merchant(client, "Merchant A")
    m2 = await _create_merchant(client, "Merchant B")

    await client.post("/api/v1/offers", json={"merchant_id": m1, "name": "A-1"})
    await client.post("/api/v1/offers", json={"merchant_id": m1, "name": "A-2"})
    await client.post("/api/v1/offers", json={"merchant_id": m2, "name": "B-1"})

    resp = await client.get(f"/api/v1/offers?merchant_id={m1}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert all(item["merchant_id"] == m1 for item in data["items"])


@pytest.mark.asyncio
async def test_list_offers_all(client: AsyncClient):
    m = await _create_merchant(client)
    await client.post("/api/v1/offers", json={"merchant_id": m, "name": "O1"})
    await client.post("/api/v1/offers", json={"merchant_id": m, "name": "O2"})

    resp = await client.get("/api/v1/offers")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 2


@pytest.mark.asyncio
async def test_update_offer(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    create_resp = await client.post("/api/v1/offers", json={
        "merchant_id": merchant_id, "name": "Old Offer"
    })
    offer_id = create_resp.json()["id"]

    resp = await client.patch(f"/api/v1/offers/{offer_id}", json={
        "name": "New Offer",
        "status": "reviewed",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "New Offer"
    assert data["status"] == "reviewed"


@pytest.mark.asyncio
async def test_update_offer_not_found(client: AsyncClient):
    resp = await client.patch(
        "/api/v1/offers/00000000-0000-0000-0000-000000000000",
        json={"name": "Ghost"},
    )
    assert resp.status_code == 404
