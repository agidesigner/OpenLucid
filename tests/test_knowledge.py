import pytest
from httpx import AsyncClient


async def _create_merchant(client: AsyncClient) -> str:
    resp = await client.post("/api/v1/merchants", json={"name": "KM"})
    return resp.json()["id"]


async def _create_offer(client: AsyncClient, merchant_id: str) -> str:
    resp = await client.post("/api/v1/offers", json={
        "merchant_id": merchant_id, "name": "KO"
    })
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_knowledge_merchant_scope(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    resp = await client.post("/api/v1/knowledge", json={
        "scope_type": "merchant",
        "scope_id": merchant_id,
        "title": "Brand Story",
        "content_raw": "We are the best.",
        "knowledge_type": "brand",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Brand Story"
    assert data["scope_type"] == "merchant"
    assert data["knowledge_type"] == "brand"
    assert data["source_type"] == "manual"


@pytest.mark.asyncio
async def test_create_knowledge_offer_scope(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer_id = await _create_offer(client, merchant_id)
    resp = await client.post("/api/v1/knowledge", json={
        "scope_type": "offer",
        "scope_id": offer_id,
        "title": "Product FAQ",
        "content_raw": "Q: How does it work? A: Magic.",
        "knowledge_type": "faq",
    })
    assert resp.status_code == 201
    assert resp.json()["scope_type"] == "offer"
    assert resp.json()["knowledge_type"] == "faq"


@pytest.mark.asyncio
async def test_create_knowledge_with_structured_content(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    resp = await client.post("/api/v1/knowledge", json={
        "scope_type": "merchant",
        "scope_id": merchant_id,
        "title": "Selling Points",
        "knowledge_type": "selling_point",
        "content_structured_json": {
            "points": ["Fast delivery", "Premium quality", "24/7 support"]
        },
        "tags_json": {"priority": "high"},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["content_structured_json"]["points"]) == 3
    assert data["tags_json"]["priority"] == "high"


@pytest.mark.asyncio
async def test_get_knowledge(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    create_resp = await client.post("/api/v1/knowledge", json={
        "scope_type": "merchant",
        "scope_id": merchant_id,
        "title": "Test Knowledge",
    })
    kid = create_resp.json()["id"]

    resp = await client.get(f"/api/v1/knowledge/{kid}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Test Knowledge"


@pytest.mark.asyncio
async def test_get_knowledge_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/knowledge/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_knowledge_by_scope(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer_id = await _create_offer(client, merchant_id)

    await client.post("/api/v1/knowledge", json={
        "scope_type": "merchant", "scope_id": merchant_id, "title": "M-K1",
    })
    await client.post("/api/v1/knowledge", json={
        "scope_type": "offer", "scope_id": offer_id, "title": "O-K1",
    })
    await client.post("/api/v1/knowledge", json={
        "scope_type": "offer", "scope_id": offer_id, "title": "O-K2",
    })

    resp = await client.get(f"/api/v1/knowledge?scope_type=offer&scope_id={offer_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2

    resp2 = await client.get(f"/api/v1/knowledge?scope_type=merchant&scope_id={merchant_id}")
    assert resp2.json()["total"] == 1


@pytest.mark.asyncio
async def test_update_knowledge(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    create_resp = await client.post("/api/v1/knowledge", json={
        "scope_type": "merchant",
        "scope_id": merchant_id,
        "title": "Old Title",
        "content_raw": "Old content",
    })
    kid = create_resp.json()["id"]

    resp = await client.patch(f"/api/v1/knowledge/{kid}", json={
        "title": "New Title",
        "content_raw": "Updated content",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "New Title"
    assert data["content_raw"] == "Updated content"


# --- Batch import ---


@pytest.mark.asyncio
async def test_batch_import_knowledge(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    resp = await client.post("/api/v1/knowledge/batch", json={
        "scope_type": "merchant",
        "scope_id": merchant_id,
        "items": [
            {"scope_type": "merchant", "scope_id": merchant_id, "title": "K1", "knowledge_type": "brand", "content_raw": "Brand info"},
            {"scope_type": "merchant", "scope_id": merchant_id, "title": "K2", "knowledge_type": "audience", "content_raw": "Audience info"},
            {"scope_type": "merchant", "scope_id": merchant_id, "title": "K3", "knowledge_type": "faq", "content_raw": "FAQ info"},
        ],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["created"] == 3
    assert len(data["items"]) == 3


@pytest.mark.asyncio
async def test_batch_import_overrides_scope(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    offer_id = await _create_offer(client, merchant_id)

    resp = await client.post("/api/v1/knowledge/batch", json={
        "scope_type": "offer",
        "scope_id": offer_id,
        "items": [
            {"scope_type": "merchant", "scope_id": merchant_id, "title": "Will be overridden"},
        ],
    })
    assert resp.status_code == 201
    # Item scope should be overridden to offer-level
    assert resp.json()["items"][0]["scope_type"] == "offer"
    assert resp.json()["items"][0]["scope_id"] == offer_id


# --- Delete ---


@pytest.mark.asyncio
async def test_delete_knowledge(client: AsyncClient):
    merchant_id = await _create_merchant(client)
    create_resp = await client.post("/api/v1/knowledge", json={
        "scope_type": "merchant",
        "scope_id": merchant_id,
        "title": "To Delete",
    })
    kid = create_resp.json()["id"]

    resp = await client.delete(f"/api/v1/knowledge/{kid}")
    assert resp.status_code == 204

    # Verify deleted
    get_resp = await client.get(f"/api/v1/knowledge/{kid}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_knowledge_not_found(client: AsyncClient):
    resp = await client.delete("/api/v1/knowledge/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
