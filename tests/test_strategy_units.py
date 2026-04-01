import pytest
from httpx import AsyncClient


async def _create_merchant(client: AsyncClient, name: str = "Test Merchant") -> str:
    resp = await client.post("/api/v1/merchants", json={"name": name})
    return resp.json()["id"]


async def _create_offer(client: AsyncClient, merchant_id: str, name: str = "Test Offer") -> str:
    resp = await client.post("/api/v1/offers", json={"merchant_id": merchant_id, "name": name})
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_strategy_unit(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer_id = await _create_offer(client, merchant_id)

    resp = await client.post("/api/v1/strategy-units", json={
        "merchant_id": merchant_id,
        "offer_id": offer_id,
        "name": "Young Women Skincare",
        "audience_segment": "18-25 female",
        "channel": "douyin",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Young Women Skincare"
    assert data["strategy_stage"] == "exploring"
    assert data["trend_status"] == "unknown"
    assert data["status"] == "active"
    assert data["asset_count"] == 0
    assert data["topic_count"] == 0


@pytest.mark.asyncio
async def test_create_with_invalid_offer(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    resp = await client.post("/api/v1/strategy-units", json={
        "merchant_id": merchant_id,
        "offer_id": "00000000-0000-0000-0000-000000000000",
        "name": "Ghost Unit",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_strategy_unit(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer_id = await _create_offer(client, merchant_id)

    create_resp = await client.post("/api/v1/strategy-units", json={
        "merchant_id": merchant_id,
        "offer_id": offer_id,
        "name": "Round-trip Unit",
    })
    unit_id = create_resp.json()["id"]

    resp = await client.get(f"/api/v1/strategy-units/{unit_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Round-trip Unit"
    assert resp.json()["offer_id"] == offer_id


@pytest.mark.asyncio
async def test_get_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/strategy-units/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_by_offer(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer1 = await _create_offer(client, merchant_id, "Offer 1")
    offer2 = await _create_offer(client, merchant_id, "Offer 2")

    await client.post("/api/v1/strategy-units", json={"merchant_id": merchant_id, "offer_id": offer1, "name": "Unit A"})
    await client.post("/api/v1/strategy-units", json={"merchant_id": merchant_id, "offer_id": offer1, "name": "Unit B"})
    await client.post("/api/v1/strategy-units", json={"merchant_id": merchant_id, "offer_id": offer2, "name": "Unit C"})

    resp = await client.get(f"/api/v1/strategy-units?offer_id={offer1}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert all(item["offer_id"] == offer1 for item in data["items"])


@pytest.mark.asyncio
async def test_update_strategy_unit(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer_id = await _create_offer(client, merchant_id)

    create_resp = await client.post("/api/v1/strategy-units", json={
        "merchant_id": merchant_id,
        "offer_id": offer_id,
        "name": "Old Name",
    })
    unit_id = create_resp.json()["id"]

    resp = await client.patch(f"/api/v1/strategy-units/{unit_id}", json={
        "name": "New Name",
        "strategy_stage": "rising",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "New Name"
    assert data["strategy_stage"] == "rising"


@pytest.mark.asyncio
async def test_delete_strategy_unit(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer_id = await _create_offer(client, merchant_id)

    create_resp = await client.post("/api/v1/strategy-units", json={
        "merchant_id": merchant_id,
        "offer_id": offer_id,
        "name": "To Delete",
    })
    unit_id = create_resp.json()["id"]

    del_resp = await client.delete(f"/api/v1/strategy-units/{unit_id}")
    assert del_resp.status_code == 204

    get_resp = await client.get(f"/api/v1/strategy-units/{unit_id}")
    assert get_resp.status_code == 404
