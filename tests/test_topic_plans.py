import io

import pytest
from httpx import AsyncClient
from PIL import Image


def _make_png() -> bytes:
    img = Image.new("RGB", (100, 100))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _setup_offer_with_context(client: AsyncClient) -> tuple[str, str]:
    """Create merchant + offer + knowledge + assets."""
    m = await client.post("/api/v1/merchants", json={
        "name": "Topic Test Merchant",
        "brand_profile_json": {"slogan": "Innovation First"},
    })
    mid = m.json()["id"]

    o = await client.post("/api/v1/offers", json={
        "merchant_id": mid,
        "name": "Smart Camera",
        "offer_type": "product",
        "description": "AI-powered security camera",
        "core_selling_points_json": {"points": ["4K resolution", "AI detection", "night vision"]},
        "target_audience_json": {"items": ["homeowners", "small businesses"]},
        "target_scenarios_json": {"items": ["home security", "office monitoring"]},
    })
    oid = o.json()["id"]

    # Knowledge
    await client.post("/api/v1/knowledge/batch", json={
        "scope_type": "offer",
        "scope_id": oid,
        "items": [
            {"scope_type": "offer", "scope_id": oid, "title": "Key Feature", "knowledge_type": "selling_point", "content_raw": "4K with HDR"},
            {"scope_type": "offer", "scope_id": oid, "title": "FAQ", "knowledge_type": "faq", "content_raw": "Works with Alexa"},
        ],
    })

    # Assets
    png = _make_png()
    await client.post(
        "/api/v1/assets/upload",
        data={"scope_type": "offer", "scope_id": oid, "asset_type": "image"},
        files={"file": ("product.png", png, "image/png")},
    )

    return mid, oid


@pytest.mark.asyncio
async def test_generate_topic_plans(client: AsyncClient):
    mid, oid = await _setup_offer_with_context(client)

    resp = await client.post("/api/v1/topic-plans/generate", json={
        "offer_id": oid,
        "count": 3,
        "channel": "douyin",
        "language": "zh-CN",
    })
    assert resp.status_code == 201
    data = resp.json()

    assert data["offer_id"] == oid
    assert data["count"] == 3
    assert len(data["plans"]) == 3

    plan = data["plans"][0]
    assert plan["merchant_id"] == mid
    assert plan["offer_id"] == oid
    assert plan["status"] == "draft"
    assert plan["language"] == "zh-CN"
    assert plan["title"]
    assert plan["hook"]
    assert plan["angle"]
    assert plan["channel"] == "douyin"
    assert plan["score_relevance"] is not None
    assert plan["score_conversion"] is not None
    assert "id" in plan
    assert "created_at" in plan


@pytest.mark.asyncio
async def test_generate_plans_persisted(client: AsyncClient):
    """Generated plans should be retrievable via list and get."""
    _, oid = await _setup_offer_with_context(client)

    gen_resp = await client.post("/api/v1/topic-plans/generate", json={
        "offer_id": oid, "count": 2,
    })
    plans = gen_resp.json()["plans"]
    plan_id = plans[0]["id"]

    # Get by ID
    get_resp = await client.get(f"/api/v1/topic-plans/{plan_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["title"] == plans[0]["title"]

    # List by offer
    list_resp = await client.get(f"/api/v1/topic-plans?offer_id={oid}")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_generate_plans_context_aware(client: AsyncClient):
    """Plans should reflect offer context (selling points, audiences)."""
    _, oid = await _setup_offer_with_context(client)

    resp = await client.post("/api/v1/topic-plans/generate", json={
        "offer_id": oid, "count": 5,
    })
    plans = resp.json()["plans"]

    all_titles = " ".join(p["title"] for p in plans)
    all_hooks = " ".join(p["hook"] for p in plans)

    # Should reference the offer name
    assert "Smart Camera" in all_titles or "Smart Camera" in all_hooks

    # Should have diverse angles
    angles = {p["angle"] for p in plans}
    assert len(angles) >= 3  # at least 3 different angles

    # Should have scores
    for p in plans:
        assert 0 <= p["score_relevance"] <= 1
        assert 0 <= p["score_conversion"] <= 1


@pytest.mark.asyncio
async def test_generate_plans_default_count(client: AsyncClient):
    _, oid = await _setup_offer_with_context(client)

    resp = await client.post("/api/v1/topic-plans/generate", json={"offer_id": oid})
    assert resp.status_code == 201
    assert resp.json()["count"] == 5  # default


@pytest.mark.asyncio
async def test_generate_plans_invalid_offer(client: AsyncClient):
    resp = await client.post("/api/v1/topic-plans/generate", json={
        "offer_id": "00000000-0000-0000-0000-000000000000",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_generate_plans_minimal_context(client: AsyncClient):
    """Should work even with an offer that has no knowledge or assets."""
    m = await client.post("/api/v1/merchants", json={"name": "Bare Merchant"})
    mid = m.json()["id"]
    o = await client.post("/api/v1/offers", json={
        "merchant_id": mid, "name": "Basic Product",
    })
    oid = o.json()["id"]

    resp = await client.post("/api/v1/topic-plans/generate", json={
        "offer_id": oid, "count": 2,
    })
    assert resp.status_code == 201
    assert resp.json()["count"] == 2


@pytest.mark.asyncio
async def test_list_topic_plans_empty(client: AsyncClient):
    resp = await client.get("/api/v1/topic-plans")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_get_topic_plan_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/topic-plans/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
