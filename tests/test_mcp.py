import json

import pytest
from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Override the MCP session factory with a NullPool test engine BEFORE importing tools
_test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)

import app.mcp_server as mcp_module  # noqa: E402

mcp_module._session_factory = _test_session_factory

from app.mcp_server import (  # noqa: E402
    add_knowledge_item,
    create_merchant,
    create_offer,
    generate_topic_plans,
    get_offer_context_summary,
    list_asset_slices,
    list_knowledge,
    list_merchants,
    search_assets,
)


def _parse(result: str) -> dict | list:
    return json.loads(result)


@pytest.mark.asyncio
async def test_mcp_create_merchant():
    result = _parse(await create_merchant(name="MCP Merchant", merchant_type="goods"))
    assert result["name"] == "MCP Merchant"
    assert "id" in result


@pytest.mark.asyncio
async def test_mcp_list_merchants():
    await create_merchant(name="List Test")
    result = _parse(await list_merchants())
    assert result["total"] >= 1


@pytest.mark.asyncio
async def test_mcp_create_offer():
    m = _parse(await create_merchant(name="Offer Parent"))
    result = _parse(await create_offer(
        merchant_id=m["id"],
        name="MCP Product",
        offer_type="product",
        core_selling_points=["fast", "reliable"],
        target_audiences=["developers"],
    ))
    assert result["name"] == "MCP Product"
    assert result["core_selling_points_json"]["points"] == ["fast", "reliable"]


@pytest.mark.asyncio
async def test_mcp_knowledge_workflow():
    m = _parse(await create_merchant(name="KB Merchant"))
    mid = m["id"]

    # Add knowledge
    k = _parse(await add_knowledge_item(
        scope_type="merchant",
        scope_id=mid,
        title="Brand Info",
        content="We build great tools",
        knowledge_type="brand",
    ))
    assert k["title"] == "Brand Info"

    # List knowledge
    result = _parse(await list_knowledge(scope_type="merchant", scope_id=mid))
    assert result["total"] >= 1


@pytest.mark.asyncio
async def test_mcp_search_assets_empty():
    m = _parse(await create_merchant(name="Asset Merchant"))
    result = _parse(await search_assets(scope_type="merchant", scope_id=m["id"]))
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_mcp_get_offer_context():
    m = _parse(await create_merchant(name="Ctx Merchant"))
    o = _parse(await create_offer(
        merchant_id=m["id"],
        name="Ctx Product",
        core_selling_points=["durable"],
    ))
    await add_knowledge_item(
        scope_type="offer", scope_id=o["id"],
        title="FAQ", content="It works", knowledge_type="faq",
    )

    result = _parse(await get_offer_context_summary(offer_id=o["id"]))
    assert result["offer"]["name"] == "Ctx Product"
    assert result["offer_knowledge"]["total"] == 1
    assert "durable" in result["selling_points"]


@pytest.mark.asyncio
async def test_mcp_generate_topic_plans():
    m = _parse(await create_merchant(name="Topic Merchant"))
    o = _parse(await create_offer(
        merchant_id=m["id"],
        name="Topic Product",
        core_selling_points=["innovative", "eco-friendly"],
        target_audiences=["millennials"],
    ))

    result = _parse(await generate_topic_plans(
        offer_id=o["id"], count=3, channel="douyin",
    ))
    assert len(result) == 3
    assert result[0]["title"]
    assert result[0]["channel"] == "douyin"
