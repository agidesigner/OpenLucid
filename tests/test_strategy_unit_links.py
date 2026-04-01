import uuid

import pytest
from httpx import AsyncClient


async def _create_merchant(client: AsyncClient, name: str = "Test Merchant") -> str:
    resp = await client.post("/api/v1/merchants", json={"name": name})
    return resp.json()["id"]


async def _create_offer(client: AsyncClient, merchant_id: str, name: str = "Test Offer") -> str:
    resp = await client.post("/api/v1/offers", json={"merchant_id": merchant_id, "name": name})
    return resp.json()["id"]


async def _create_strategy_unit(client: AsyncClient, merchant_id: str, offer_id: str, name: str = "Test SU") -> str:
    resp = await client.post("/api/v1/strategy-units", json={
        "merchant_id": merchant_id,
        "offer_id": offer_id,
        "name": name,
    })
    return resp.json()["id"]


async def _create_knowledge_item(client: AsyncClient, scope_id: str, title: str = "Test KI") -> str:
    resp = await client.post("/api/v1/knowledge", json={
        "scope_type": "offer",
        "scope_id": scope_id,
        "title": title,
    })
    return resp.json()["id"]


async def _create_asset(client: AsyncClient, scope_id: str) -> str:
    import io
    files = {"file": ("test.txt", io.BytesIO(b"test content"), "text/plain")}
    data = {"scope_type": "offer", "scope_id": scope_id, "asset_type": "document"}
    resp = await client.post("/api/v1/assets/upload", files=files, data=data)
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_knowledge_link(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer_id = await _create_offer(client, merchant_id)
    su_id = await _create_strategy_unit(client, merchant_id, offer_id)
    ki_id = await _create_knowledge_item(client, offer_id)

    resp = await client.post(f"/api/v1/strategy-units/{su_id}/knowledge-links", json={
        "knowledge_item_id": ki_id,
        "role": "core_message",
        "priority": 10,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["strategy_unit_id"] == su_id
    assert data["knowledge_item_id"] == ki_id
    assert data["role"] == "core_message"
    assert data["priority"] == 10


@pytest.mark.asyncio
async def test_duplicate_knowledge_link(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer_id = await _create_offer(client, merchant_id)
    su_id = await _create_strategy_unit(client, merchant_id, offer_id)
    ki_id = await _create_knowledge_item(client, offer_id)

    resp1 = await client.post(f"/api/v1/strategy-units/{su_id}/knowledge-links", json={
        "knowledge_item_id": ki_id,
    })
    assert resp1.status_code == 201

    resp2 = await client.post(f"/api/v1/strategy-units/{su_id}/knowledge-links", json={
        "knowledge_item_id": ki_id,
    })
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_list_knowledge_links(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer_id = await _create_offer(client, merchant_id)
    su_id = await _create_strategy_unit(client, merchant_id, offer_id)
    ki1_id = await _create_knowledge_item(client, offer_id, "KI 1")
    ki2_id = await _create_knowledge_item(client, offer_id, "KI 2")

    await client.post(f"/api/v1/strategy-units/{su_id}/knowledge-links", json={
        "knowledge_item_id": ki1_id, "priority": 5,
    })
    await client.post(f"/api/v1/strategy-units/{su_id}/knowledge-links", json={
        "knowledge_item_id": ki2_id, "priority": 10,
    })

    resp = await client.get(f"/api/v1/strategy-units/{su_id}/knowledge-links")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    # Ordered by priority desc — ki2 (priority=10) should be first
    assert data["items"][0]["knowledge_item_id"] == ki2_id


@pytest.mark.asyncio
async def test_delete_knowledge_link(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer_id = await _create_offer(client, merchant_id)
    su_id = await _create_strategy_unit(client, merchant_id, offer_id)
    ki_id = await _create_knowledge_item(client, offer_id)

    create_resp = await client.post(f"/api/v1/strategy-units/{su_id}/knowledge-links", json={
        "knowledge_item_id": ki_id,
    })
    link_id = create_resp.json()["id"]

    del_resp = await client.delete(f"/api/v1/strategy-units/{su_id}/knowledge-links/{link_id}")
    assert del_resp.status_code == 204

    list_resp = await client.get(f"/api/v1/strategy-units/{su_id}/knowledge-links")
    assert list_resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_create_asset_link(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer_id = await _create_offer(client, merchant_id)
    su_id = await _create_strategy_unit(client, merchant_id, offer_id)
    asset_id = await _create_asset(client, offer_id)

    resp = await client.post(f"/api/v1/strategy-units/{su_id}/asset-links", json={
        "asset_id": asset_id,
        "role": "hook_asset",
        "priority": 5,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["strategy_unit_id"] == su_id
    assert data["asset_id"] == asset_id
    assert data["role"] == "hook_asset"


@pytest.mark.asyncio
async def test_link_invalid_strategy_unit(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer_id = await _create_offer(client, merchant_id)
    ki_id = await _create_knowledge_item(client, offer_id)
    fake_su = str(uuid.uuid4())

    resp = await client.post(f"/api/v1/strategy-units/{fake_su}/knowledge-links", json={
        "knowledge_item_id": ki_id,
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_link_invalid_knowledge_item(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer_id = await _create_offer(client, merchant_id)
    su_id = await _create_strategy_unit(client, merchant_id, offer_id)
    fake_ki = str(uuid.uuid4())

    resp = await client.post(f"/api/v1/strategy-units/{su_id}/knowledge-links", json={
        "knowledge_item_id": fake_ki,
    })
    assert resp.status_code == 404
