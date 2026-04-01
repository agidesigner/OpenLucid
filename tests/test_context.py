import pytest
from httpx import AsyncClient


async def _setup_full_offer(client: AsyncClient) -> tuple[str, str]:
    """Create merchant + offer with knowledge and assets, return (merchant_id, offer_id)."""
    m_resp = await client.post("/api/v1/merchants", json={
        "name": "Context Test Merchant",
        "merchant_type": "goods",
        "brand_profile_json": {"slogan": "Quality First"},
    })
    merchant_id = m_resp.json()["id"]

    o_resp = await client.post("/api/v1/offers", json={
        "merchant_id": merchant_id,
        "name": "Premium Widget",
        "offer_type": "product",
        "description": "A premium widget for professionals",
        "core_selling_points_json": {"points": ["durable", "lightweight", "affordable"]},
        "target_audience_json": {"items": ["professionals", "hobbyists"]},
        "target_scenarios_json": {"items": ["office", "outdoor"]},
    })
    offer_id = o_resp.json()["id"]

    # Add merchant-level knowledge
    await client.post("/api/v1/knowledge", json={
        "scope_type": "merchant",
        "scope_id": merchant_id,
        "title": "Brand Story",
        "content_raw": "Founded in 2020, we focus on quality.",
        "knowledge_type": "brand",
    })
    await client.post("/api/v1/knowledge", json={
        "scope_type": "merchant",
        "scope_id": merchant_id,
        "title": "Target Market",
        "content_raw": "B2B professionals aged 25-45",
        "knowledge_type": "audience",
    })

    # Add offer-level knowledge
    await client.post("/api/v1/knowledge", json={
        "scope_type": "offer",
        "scope_id": offer_id,
        "title": "Product FAQ",
        "content_raw": "Q: Is it waterproof? A: Yes.",
        "knowledge_type": "faq",
    })
    await client.post("/api/v1/knowledge", json={
        "scope_type": "offer",
        "scope_id": offer_id,
        "title": "Key Selling Point",
        "content_raw": "30% lighter than competitors",
        "knowledge_type": "selling_point",
    })

    # Add assets at merchant level
    import io
    from PIL import Image
    img = Image.new("RGB", (100, 100))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()

    await client.post(
        "/api/v1/assets/upload",
        data={"scope_type": "merchant", "scope_id": merchant_id, "asset_type": "image"},
        files={"file": ("brand.png", png, "image/png")},
    )

    # Add asset at offer level
    await client.post(
        "/api/v1/assets/upload",
        data={"scope_type": "offer", "scope_id": offer_id, "asset_type": "image"},
        files={"file": ("product.png", png, "image/png")},
    )
    await client.post(
        "/api/v1/assets/upload",
        data={"scope_type": "offer", "scope_id": offer_id, "asset_type": "video"},
        files={"file": ("demo.mp4", b"fake-video", "video/mp4")},
    )

    return merchant_id, offer_id


@pytest.mark.asyncio
async def test_get_offer_context(client: AsyncClient):
    merchant_id, offer_id = await _setup_full_offer(client)

    resp = await client.get(f"/api/v1/offers/{offer_id}/context")
    assert resp.status_code == 200
    data = resp.json()

    # Offer & Merchant present
    assert data["offer"]["id"] == offer_id
    assert data["merchant"]["id"] == merchant_id
    assert data["merchant"]["name"] == "Context Test Merchant"

    # Knowledge counts
    assert data["merchant_knowledge"]["total"] == 2
    assert "brand" in data["merchant_knowledge"]["by_type"]
    assert data["offer_knowledge"]["total"] == 2
    assert "faq" in data["offer_knowledge"]["by_type"]

    # All knowledge items returned
    assert len(data["knowledge_items"]) == 4

    # Asset counts
    assert data["merchant_assets"]["total"] == 1
    assert data["offer_assets"]["total"] == 2
    assert data["offer_assets"]["by_type"]["image"] == 1
    assert data["offer_assets"]["by_type"]["video"] == 1

    # All assets returned
    assert len(data["assets"]) == 3

    # Derived context
    assert "durable" in data["selling_points"]
    assert "professionals" in data["target_audiences"]
    assert "office" in data["target_scenarios"]


@pytest.mark.asyncio
async def test_get_offer_context_empty(client: AsyncClient):
    """Context for offer with no knowledge or assets."""
    m_resp = await client.post("/api/v1/merchants", json={"name": "Empty Merchant"})
    merchant_id = m_resp.json()["id"]
    o_resp = await client.post("/api/v1/offers", json={
        "merchant_id": merchant_id, "name": "Empty Offer"
    })
    offer_id = o_resp.json()["id"]

    resp = await client.get(f"/api/v1/offers/{offer_id}/context")
    assert resp.status_code == 200
    data = resp.json()
    assert data["merchant_knowledge"]["total"] == 0
    assert data["offer_knowledge"]["total"] == 0
    assert data["merchant_assets"]["total"] == 0
    assert data["offer_assets"]["total"] == 0
    assert len(data["knowledge_items"]) == 0
    assert len(data["assets"]) == 0


@pytest.mark.asyncio
async def test_get_offer_context_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/offers/00000000-0000-0000-0000-000000000000/context")
    assert resp.status_code == 404
